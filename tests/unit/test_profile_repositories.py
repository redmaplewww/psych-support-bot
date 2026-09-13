"""画像 belief 仓储层单测。

覆盖：
1. 合并策略：created / supported（证据去重合并 + 置信推进封顶）/
   contradicted / downgraded（L2 降级回 L4）。
2. 结构性红线：extracted 来源强制 L4；distortion.* key 拒绝沉淀；
   否决后同 key 复活守卫（不新建行、不记事件）。
3. 确认/否决原语（K2 质询闭环的升级路径）。
4. 级联删除（/v1/me 删除链）与 P2 统计行。
"""

from uuid import uuid4

import pytest

from psych_support_bot.infra.db.models import ProfileBelief
from psych_support_bot.infra.db.profile_repositories import (
    CONFIDENCE_CEILING,
    confirm_belief,
    delete_user_profile_beliefs,
    get_belief,
    get_belief_events,
    list_active_beliefs,
    record_claim,
    record_extraction_stats,
    reject_belief,
)
from psych_support_bot.infra.db.session import SessionLocal


def _uid() -> str:
    return f"pb-{uuid4().hex[:8]}"


def _claim(user_id: str, *, key: str = "rumination_loop", **overrides):
    params = {
        "dimension": "D3",
        "key": key,
        "claim_text": "测试观察句",
        "confidence": 0.4,
        "session_id": f"s-{uuid4().hex[:8]}",
        "evidence_message_ids": [101],
    }
    params.update(overrides)
    with SessionLocal() as session:
        belief, event = record_claim(session, user_id, **params)
        session.commit()
        return (belief.id if belief is not None else None), event


# --- 合并策略 ---


def test_created_claim_starts_active_with_event() -> None:
    user_id = _uid()
    belief_id, event = _claim(user_id)
    assert event == "created"
    with SessionLocal() as session:
        belief = session.get(ProfileBelief, belief_id)
        assert belief.layer == "L4"
        assert belief.status == "active"
        assert belief.confidence == 0.4
        events = get_belief_events(session, user_id, belief_id)
        assert [e.event_type for e in events] == ["created"]


def test_support_merges_evidence_and_raises_confidence() -> None:
    user_id = _uid()
    belief_id, first = _claim(user_id, evidence_message_ids=[1, 2])
    assert first == "created"
    belief_id2, second = _claim(user_id, evidence_message_ids=[2, 3])
    assert belief_id2 == belief_id
    assert second == "supported"
    with SessionLocal() as session:
        belief = session.get(ProfileBelief, belief_id)
        assert belief.confidence == pytest.approx(0.55)
        assert belief.evidence_json == "[1, 2, 3]"  # 证据去重合并
        # 事件按最新在前（仓储层列表函数统一约定）
        assert [e.event_type for e in get_belief_events(session, user_id, belief_id)] == ["supported", "created"]


def test_confidence_capped_at_ceiling() -> None:
    user_id = _uid()
    belief_id, _ = _claim(user_id, confidence=0.9)
    for _ in range(5):
        belief_id, _ = _claim(user_id, key="rumination_loop")
    with SessionLocal() as session:
        belief = session.get(ProfileBelief, belief_id)
        assert belief.confidence == CONFIDENCE_CEILING


def test_contradicts_decays_confidence() -> None:
    user_id = _uid()
    belief_id, _ = _claim(user_id, confidence=0.5)
    _, event = _claim(user_id, relation="contradicts", confidence=0.9)
    assert event == "contradicted"
    with SessionLocal() as session:
        belief = session.get(ProfileBelief, belief_id)
        assert belief.confidence == pytest.approx(0.25)


def test_contradicts_downgrades_l2_to_l4() -> None:
    user_id = _uid()
    # 程序硬证据（非 extracted）允许直接以 L2 起步：如量表记录推导的信念。
    belief_id, created = _claim(user_id, source="program", layer="L2", confidence=0.8)
    assert created == "created"
    _, event = _claim(user_id, relation="contradicts")
    assert event == "downgraded"
    with SessionLocal() as session:
        belief = session.get(ProfileBelief, belief_id)
        assert belief.layer == "L4"
        assert belief.confidence == pytest.approx(0.4)


# --- 结构性红线 ---


def test_extracted_source_clamped_to_l4() -> None:
    user_id = _uid()
    belief_id, event = _claim(user_id, layer="L2")  # 提取器试图直接写 L2
    assert event == "created"
    with SessionLocal() as session:
        belief = session.get(ProfileBelief, belief_id)
        assert belief.source == "extracted"
        assert belief.layer == "L4"


def test_distortion_key_never_persists() -> None:
    user_id = _uid()
    with pytest.raises(ValueError, match="identify-only"):
        _claim(user_id, key="distortion.labeling")
    with SessionLocal() as session:
        assert get_belief(session, user_id, "distortion.labeling") is None


def test_rejected_key_never_resurrects_and_leaves_no_trace() -> None:
    user_id = _uid()
    belief_id, _ = _claim(user_id)
    with SessionLocal() as session:
        assert reject_belief(session, user_id, belief_id) is not None
        session.commit()
    belief_id2, event = _claim(user_id)  # 换措辞？key 相同即守卫
    assert belief_id2 is None
    assert event == "resurrection_guard"
    with SessionLocal() as session:
        belief = get_belief(session, user_id, "rumination_loop")
        assert belief.status == "rejected"
        # 守卫零痕迹：否决事件仍是最后一条，未被试探刷出新事件（最新在前）
        assert [e.event_type for e in get_belief_events(session, user_id, belief_id)] == ["rejected", "created"]


# --- 确认 / 否决 / 查询 ---


def test_confirm_promotes_l4_to_l2() -> None:
    user_id = _uid()
    belief_id, _ = _claim(user_id, confidence=0.5)
    with SessionLocal() as session:
        confirmed = confirm_belief(session, user_id, belief_id)
        session.commit()
        assert confirmed is not None
        assert confirmed.layer == "L2"
        assert confirmed.source == "user_confirmed"
        assert confirmed.confidence >= 0.9


def test_confirm_reject_cross_user_guard() -> None:
    user_id = _uid()
    other = _uid()
    belief_id, _ = _claim(user_id)
    with SessionLocal() as session:
        assert confirm_belief(session, other, belief_id) is None
        assert reject_belief(session, other, belief_id) is None


def test_list_active_excludes_rejected_and_filters_dimension() -> None:
    user_id = _uid()
    belief_id, _ = _claim(user_id, key="control_struggle")
    _claim(user_id, key="worry_uncontrollable", dimension="D2")
    with SessionLocal() as session:
        reject_belief(session, user_id, belief_id)
        session.commit()
        d3 = list_active_beliefs(session, user_id, dimensions=("D3",))
        assert [b.key for b in d3] == []
        d2 = list_active_beliefs(session, user_id, dimensions=("D2",))
        assert [b.key for b in d2] == ["worry_uncontrollable"]
        assert list_active_beliefs(session, user_id) or True  # 不抛错即可


# --- 级联删除 / P2 统计 ---


def test_delete_user_profile_beliefs_cascades() -> None:
    user_id = _uid()
    _, _ = _claim(user_id)
    with SessionLocal() as session:
        record_extraction_stats(session, user_id, trigger="turn_threshold", model="test-model")
        session.commit()
        counts = delete_user_profile_beliefs(session, user_id)
        session.commit()
        assert counts["profile_beliefs"] == 1
        assert counts["profile_belief_events"] == 1
        assert counts["profile_extraction_stats"] == 1
        assert get_belief(session, user_id, "rumination_loop") is None


def test_invalid_dimension_and_relation_rejected() -> None:
    user_id = _uid()
    with pytest.raises(ValueError, match="dimension"):
        _claim(user_id, dimension="D9")
    with pytest.raises(ValueError, match="relation"):
        _claim(user_id, relation="overrides")
