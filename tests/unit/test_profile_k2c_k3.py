"""K2c 干预→反应事件 + K3a 保护因子门控 + K3b 画像面板单测。"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from psych_support_bot.ai.profile.extractor import record_turn_interventions
from psych_support_bot.ai.profile.panel import build_profile_panel
from psych_support_bot.infra.db.models import ProfileInterventionEvent
from psych_support_bot.infra.db.profile_repositories import (
    delete_user_profile_beliefs,
    get_belief,
    list_question_candidates,
    record_claim,
    record_intervention_event,
    record_protective_belief,
    reject_belief,
)
from psych_support_bot.infra.db.repositories import upsert_user_profile
from psych_support_bot.infra.db.session import SessionLocal


def _uid() -> str:
    return f"k3-{uuid4().hex[:8]}"


def _events_for(session, user_id: str):
    from sqlalchemy import select

    stmt = select(ProfileInterventionEvent).where(ProfileInterventionEvent.user_id == user_id)
    return list(session.scalars(stmt))


def _seed(user_id: str, *, key: str, dimension: str, confidence: float = 0.8, **kwargs):
    with SessionLocal() as session:
        record_claim(session, user_id, dimension=dimension, key=key, claim_text="x", confidence=confidence, **kwargs)
        session.commit()


# --- K2c 干预→反应事件 ---


def test_practice_and_question_events_recorded() -> None:
    user_id = _uid()
    with SessionLocal() as session:
        record_turn_interventions(
            session,
            user_id=user_id,
            session_id="s1",
            practice_action="offer",
            exercise_tag=None,
            question_candidates=["睡不好、睡不安稳"],
            no_question_mode=False,
            mode="support",
            risk_level="low",
        )
        session.commit()
        kinds = [e.intervention_kind for e in _events_for(session, user_id)]
    assert kinds == ["practice_offer", "question_injected"]


def test_question_event_gated_by_no_question_and_crisis() -> None:
    user_id = _uid()
    with SessionLocal() as session:
        record_turn_interventions(
            session,
            user_id=user_id,
            session_id="s1",
            practice_action="",
            exercise_tag=None,
            question_candidates=["睡不好、睡不安稳"],
            no_question_mode=True,
            mode="support",
            risk_level="low",
        )
        record_turn_interventions(
            session,
            user_id=user_id,
            session_id="s2",
            practice_action="",
            exercise_tag=None,
            question_candidates=["睡不好、睡不安稳"],
            no_question_mode=False,
            mode="crisis",
            risk_level="high",
        )
        session.commit()
        assert _events_for(session, user_id) == []


def test_intervention_events_cascade_delete() -> None:
    user_id = _uid()
    with SessionLocal() as session:
        record_intervention_event(session, user_id, session_id="s1", kind="practice_offer")
        session.commit()
        counts = delete_user_profile_beliefs(session, user_id)
        session.commit()
        assert counts["profile_intervention_events"] == 1
        assert _events_for(session, user_id) == []


# --- K3a 保护因子同意门控 ---


def test_protective_belief_requires_consent() -> None:
    user_id = _uid()
    with SessionLocal() as session:
        denied = record_protective_belief(session, user_id, "protective.reasons_for_living", consented=False)
        session.commit()
        assert denied is None
        assert get_belief(session, user_id, "protective.reasons_for_living") is None


def test_protective_belief_namespace_guard() -> None:
    with SessionLocal() as session, pytest.raises(ValueError, match="protective"):
        record_protective_belief(session, _uid(), "sleep", consented=True)


def test_protective_belief_consented_writes_l1_and_panels() -> None:
    user_id = _uid()
    with SessionLocal() as session:
        belief = record_protective_belief(
            session,
            user_id,
            "protective.reasons_for_living",
            consented=True,
            session_id="s1",
        )
        session.commit()
        assert belief is not None and belief.layer == "L1" and belief.dimension == "D7"
        panel = build_profile_panel(session, user_id)
    d7 = next(s for s in panel["sections"] if s["dimension"] == "D7")
    assert d7["items"][0]["label"] == "支撑你走下去的理由"
    assert panel["avatar"]["familiarity"] >= 1


# --- K3b 画像面板 ---


def test_panel_dimension_gating() -> None:
    user_id = _uid()
    _seed(user_id, key="sleep", dimension="D1", confidence=0.9)  # D1 展示
    _seed(user_id, key="worry_uncontrollable", dimension="D2", confidence=0.9)  # D2 默认隐藏
    _seed(user_id, key="control_struggle", dimension="D3", confidence=0.9)  # D3 未认领 → 隐藏
    _seed(
        user_id,
        key="rumination_loop",
        dimension="D3",
        confidence=0.9,
        source="user_confirmed",
        layer="L2",
    )  # D3 已认领 → 展示
    _seed(user_id, key="grief", dimension="D1", confidence=0.4)  # L4 低置信 → 面板不出
    with SessionLocal() as session:
        panel = build_profile_panel(session, user_id)
    dims = {s["dimension"] for s in panel["sections"]}
    assert "D2" not in dims
    d3 = next((s for s in panel["sections"] if s["dimension"] == "D3"), None)
    assert d3 is not None and [i["key"] for i in d3["items"]] == ["rumination_loop"]
    d1 = next(s for s in panel["sections"] if s["dimension"] == "D1")
    assert [i["key"] for i in d1["items"]] == ["sleep"]  # 0.4 低置信不出面板
    headers = [s["header"] for s in panel["sections"]]
    assert "最近在关注" in headers and "一起留意到的模式" in headers


def test_panel_boundary_section_and_avatar() -> None:
    user_id = _uid()
    _seed(user_id, key="relationships", dimension="D1", confidence=0.9)
    with SessionLocal() as session:
        reject_belief(session, user_id, get_belief(session, user_id, "relationships").id)
        session.commit()
        panel = build_profile_panel(session, user_id)
    d8 = next(s for s in panel["sections"] if s["dimension"] == "D8")
    assert "关系里的烦恼和孤单" in d8["items"][0]["label"]
    assert panel["avatar"]["familiarity"] == 1


def test_panel_endpoint_via_api() -> None:
    from psych_support_bot.app import app

    user_id = _uid()
    with SessionLocal() as session:
        _seed(user_id, key="sleep", dimension="D1", confidence=0.9)
        upsert_user_profile(session, user_id, "小测", "", "", "", "")
        session.commit()
    client = TestClient(app)
    resp = client.get("/v1/me/profile-panel", params={"user_id": user_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["sections"][0]["header"] == "最近在关注"
    assert data["avatar"]["familiarity"] >= 1


def test_unanswered_injection_blocks_reinjection_then_verdict_reopens() -> None:
    """回半环遮蔽 bug 回归：未回应的注入期间不得重复注入，判定后重新开放。"""
    user_id = _uid()
    _seed(user_id, key="sleep", dimension="D1", confidence=0.85)  # 过质询水位
    with SessionLocal() as session:
        for _ in range(2):  # 两轮都有候选：第一轮注入，第二轮未回应 → 不得重复
            record_turn_interventions(
                session, user_id=user_id, session_id="s1", practice_action="",
                exercise_tag=None, question_candidates=["睡不好、睡不安稳"],
                no_question_mode=False, mode="support", risk_level="low",
            )
        session.commit()
        injected = [e for e in _events_for(session, user_id) if e.intervention_kind == "question_injected"]
        assert len(injected) == 1
        # 判定（unclear 也算回应）→ 重新开放注入
        from psych_support_bot.infra.db.profile_repositories import confirm_belief

        belief = get_belief(session, user_id, "sleep")
        confirm_belief(session, user_id, belief.id)
        record_intervention_event(
            session, user_id, session_id="s1", kind="question_answered",
            detail={"verdict": "confirm", "belief_key": belief.key},
        )
        record_turn_interventions(
            session, user_id=user_id, session_id="s1", practice_action="",
            exercise_tag=None, question_candidates=["睡不好、睡不安稳"],
            no_question_mode=False, mode="support", risk_level="low",
        )
        session.commit()
        injected = [e for e in _events_for(session, user_id) if e.intervention_kind == "question_injected"]
        assert len(injected) == 2


def test_panel_d4_single_worked_feedback_shows() -> None:
    """调参 C 回归：用户亲口一次 worked（0.55 起步）即达面板水位。"""
    user_id = _uid()
    _seed(
        user_id,
        key="dbt_tipp",
        dimension="D4",
        confidence=0.55,
        value={"tag": "dbt_tipp", "effect": "worked"},
    )
    with SessionLocal() as session:
        panel = build_profile_panel(session, user_id)
    d4 = next(s for s in panel["sections"] if s["dimension"] == "D4")
    assert d4["items"][0]["detail"] == "用起来有帮助"


def test_question_candidates_threshold_still_holds() -> None:
    user_id = _uid()
    _seed(user_id, key="sleep", dimension="D1", confidence=0.75)
    _seed(user_id, key="grief", dimension="D1", confidence=0.6)
    with SessionLocal() as session:
        assert [b.key for b in list_question_candidates(session, user_id)] == ["sleep"]
