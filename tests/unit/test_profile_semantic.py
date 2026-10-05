"""K2 LLM 语义提取 + 质询闭环单测（不调真实 LLM）。

覆盖：
1. parse_extraction_json：合法/围栏/畸形 JSON、维度与 key 闭集校验、
   distortion 丢弃、confidence 钳位、总量 ≤3。
2. ALLOWED_KEYS：D1=主题闭集；D3 不含 distortion；锚 key 全覆盖。
3. 节流：练习事件/主题≥2/每 N 轮；危机轮不调用。
4. 端到端：monkeypatch LLM 输出 → belief L4 落库 + llm_semantic 统计行；
   LLM 不可用 → error 统计不崩溃。
5. 质询闭环：候选水位、loop_hint 注入与 no_question_mode/危机门控。
"""

from uuid import uuid4

from psych_support_bot.ai.knowledge.index import TOPIC_KEYWORDS
from psych_support_bot.ai.nodes.consultation_planner import plan_consultation
from psych_support_bot.ai.profile.extractor import record_turn_interventions, run_turn_extraction
from psych_support_bot.ai.profile.semantic import (
    ALLOWED_KEYS,
    _should_llm_extract,
    build_extraction_payload,
    parse_extraction_json,
)
from psych_support_bot.ai.schemas.messages import RiskResult
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.profile_repositories import get_belief
from psych_support_bot.infra.db.session import SessionLocal


def _uid() -> str:
    return f"ps-{uuid4().hex[:8]}"


def _stats_for(session, user_id: str):
    from sqlalchemy import select

    from psych_support_bot.infra.db.models import ProfileExtractionStats

    stmt = select(ProfileExtractionStats).where(ProfileExtractionStats.user_id == user_id)
    return list(session.scalars(stmt))


def _run(
    user_id: str, session_id: str, *, topics=None, risk_level="low", exercise_tag=None, valence_text="", turn_count=1
):
    with SessionLocal() as session:
        run_turn_extraction(
            session,
            user_id=user_id,
            session_id=session_id,
            topics=list(topics or []),
            risk_level=risk_level,
            exercise_tag=exercise_tag,
            valence_text=valence_text,
            turn_count=turn_count,
        )


# --- parse_extraction_json ---


def test_parse_valid_and_fenced_json() -> None:
    raw = '{"claims": [{"dimension": "D3", "key": "control_struggle", "claim_zh": "观察句", "confidence": 0.8, "relation": "supports"}]}'
    claims = parse_extraction_json(raw)
    assert len(claims) == 1 and claims[0]["key"] == "control_struggle" and claims[0]["confidence"] == 0.8
    fenced = f"```json\n{raw}\n```"
    assert len(parse_extraction_json(fenced)) == 1


def test_parse_garbage_and_malformed_return_empty() -> None:
    assert parse_extraction_json("not json at all") == []
    assert parse_extraction_json("") == []
    assert parse_extraction_json('{"claims": "oops"}') == []
    assert parse_extraction_json('{"claims": [{"dimension": "D9", "key": "x"}]}') == []


def test_parse_drops_invalid_keys_and_distortion() -> None:
    raw = (
        '{"claims": ['
        '{"dimension": "D3", "key": "distortion.labeling", "claim_zh": "x", "confidence": 0.9},'
        '{"dimension": "D5", "key": "not_in_list", "claim_zh": "x", "confidence": 0.9},'
        '{"dimension": "D3", "key": "control_struggle", "claim_zh": "ok", "confidence": 5, "relation": "overrides"},'
        '{"dimension": "D3", "key": "cognitive_fusion", "claim_zh": "ok", "confidence": 1.4}'
        "]}"
    )
    claims = parse_extraction_json(raw)
    assert [c["key"] for c in claims] == ["cognitive_fusion"]
    assert claims[0]["confidence"] == 1.0  # 钳位
    assert claims[0]["relation"] == "supports"  # 非法 relation 归位默认值


def test_parse_caps_at_three() -> None:
    claims_json = ",".join('{"dimension": "D1", "key": "sleep", "claim_zh": "x", "confidence": 0.5}' for _ in range(6))
    assert len(parse_extraction_json(f'{{"claims": [{claims_json}]}}')) == 3


# --- allowed keys / payload ---


def test_allowed_keys_closed_sets() -> None:
    assert ALLOWED_KEYS["D1"] == frozenset(TOPIC_KEYWORDS)
    assert all(not key.startswith("distortion.") for key in ALLOWED_KEYS["D3"])
    assert "worry_uncontrollable" in ALLOWED_KEYS["D2"]
    assert "change_talk.desire" in ALLOWED_KEYS["D5"]


def test_payload_contains_keys_beliefs_and_user_text() -> None:
    user_id = _uid()
    with SessionLocal() as session:
        from psych_support_bot.infra.db.profile_repositories import record_claim

        record_claim(session, user_id, dimension="D3", key="rumination_loop", claim_text="x", confidence=0.6)
        session.commit()
        payload = build_extraction_payload("最近总是睡不着", session, user_id)
    assert "D3 control_struggle" in payload and "D1 sleep" in payload
    assert "rumination_loop" in payload  # 当前信念进 prompt（冲突消解依据）
    assert "最近总是睡不着" in payload


# --- 节流 ---


def test_throttle_rules() -> None:
    assert _should_llm_extract("text", practice_event=True, turn_count=1)
    assert _should_llm_extract("我焦虑睡不着", practice_event=False, turn_count=1)  # 主题≥2
    assert not _should_llm_extract("我做完了", practice_event=False, turn_count=1)  # 0 主题
    # 每 N 轮且有 ≥1 主题
    assert _should_llm_extract("有点焦虑", practice_event=False, turn_count=3)
    assert not _should_llm_extract("有点焦虑", practice_event=False, turn_count=2)


# --- 端到端（monkeypatch LLM） ---


def test_semantic_extraction_merges_llm_claims(monkeypatch) -> None:
    user_id = _uid()
    session_id = f"s-{uuid4().hex[:8]}"
    canned = (
        '{"claims": ['
        '{"dimension": "D3", "key": "avoidance_maintenance.social", "claim_zh": "社交场合常先躲开", '
        '"confidence": 0.8, "relation": "supports"},'
        '{"dimension": "D1", "key": "panic", "claim_zh": "反复心慌", "confidence": 0.5}'
        "]}"
    )
    monkeypatch.setattr(
        "psych_support_bot.ai.profile.semantic.generate_profile_extraction",
        lambda **kwargs: canned,
    )
    monkeypatch.setattr(get_settings(), "profile_llm_extraction_enabled", True)  # conftest 默认关，本用例显式开
    _run(user_id, session_id, topics=[], exercise_tag=None, valence_text="最近有点焦虑", turn_count=3)
    with SessionLocal() as session:
        belief = get_belief(session, user_id, "avoidance_maintenance.social")
        assert belief is not None and belief.layer == "L4"  # extracted 强制 L4
        assert belief.source == "extracted"
        rows = _stats_for(session, user_id)
        semantic_rows = [r for r in rows if r.trigger == "llm_semantic"]
        assert len(semantic_rows) == 1 and semantic_rows[0].claims_out == 2
        assert any(r.trigger == "topic_flow" for r in rows)  # 确定性层照常记录


def test_llm_unavailable_records_error_and_does_not_crash(monkeypatch) -> None:
    user_id = _uid()
    session_id = f"s-{uuid4().hex[:8]}"

    def _boom(**kwargs):
        from psych_support_bot.infra.llm.generation import LLMUnavailableError

        raise LLMUnavailableError("provider down")

    monkeypatch.setattr("psych_support_bot.ai.profile.semantic.generate_profile_extraction", _boom)
    monkeypatch.setattr(get_settings(), "profile_llm_extraction_enabled", True)
    _run(user_id, session_id, topics=[], exercise_tag=None, valence_text="最近有点焦虑", turn_count=3)
    with SessionLocal() as session:
        rows = [r for r in _stats_for(session, user_id) if r.trigger == "llm_semantic"]
        assert len(rows) == 1 and rows[0].status == "error" and rows[0].error == "llm_unavailable_fallback"


def test_crisis_turn_skips_llm_entirely(monkeypatch) -> None:
    user_id = _uid()
    monkeypatch.setattr(get_settings(), "profile_llm_extraction_enabled", True)
    calls: list[int] = []
    monkeypatch.setattr(
        "psych_support_bot.ai.profile.semantic.generate_profile_extraction",
        lambda **kwargs: calls.append(1) or '{"claims": []}',
    )
    _run(user_id, f"s-{uuid4().hex[:8]}", topics=["sleep"], risk_level="high", valence_text="x", turn_count=3)
    with SessionLocal() as session:
        # 危机轮：确定性层记 crisis_guard，语义层完全不调用（零 llm_semantic 行）
        assert all(r.trigger != "llm_semantic" for r in _stats_for(session, user_id))
    assert calls == []


# --- 回半环：质询应答判定 ---


def _save_user_message(session_id: str, content: str) -> None:
    """生产时序：_finalize 前用户消息已落库——回半环的"首次回应"计数依赖它。"""
    from datetime import UTC, datetime

    from psych_support_bot.infra.db.models import Message

    with SessionLocal() as session:
        session.add(Message(session_id=session_id, role="user", content=content, created_at=datetime.now(UTC)))
        session.commit()


def _seed_verification_scenario(user_id: str, session_id: str) -> None:
    """播种：一条过水位的 L4 假设 + 上一轮的质询注入事件。"""
    with SessionLocal() as session:
        from psych_support_bot.infra.db.profile_repositories import record_claim

        record_claim(session, user_id, dimension="D1", key="sleep", claim_text="睡不安稳的观察", confidence=0.75)
        session.commit()
        record_turn_interventions(
            session,
            user_id=user_id,
            session_id=session_id,
            practice_action="",
            exercise_tag=None,
            question_candidates=["睡不好、睡不安稳"],
            no_question_mode=False,
            mode="support",
            risk_level="low",
        )


def test_verification_confirm_promotes_l4_to_l2(monkeypatch) -> None:
    user_id = _uid()
    session_id = f"s-{uuid4().hex[:8]}"
    _seed_verification_scenario(user_id, session_id)
    monkeypatch.setattr(get_settings(), "profile_llm_extraction_enabled", True)
    monkeypatch.setattr(
        "psych_support_bot.ai.profile.semantic.generate_profile_extraction",
        lambda **kwargs: '{"verdict": "confirm", "reason_zh": "用户亲口认可"}',
    )
    _save_user_message(session_id, "对，就是这样，我最近一直睡不好")
    _run(user_id, session_id, topics=[], exercise_tag=None, valence_text="对，就是这样，我最近一直睡不好", turn_count=3)
    with SessionLocal() as session:
        belief = get_belief(session, user_id, "sleep")
        assert belief.layer == "L2" and belief.source == "user_confirmed"
        rows = [r for r in _stats_for(session, user_id) if r.trigger == "llm_verification"]
        assert len(rows) == 1 and rows[0].claims_out == 1
        # 回半环占用本轮：常规语义提取被跳过
        assert all(r.trigger != "llm_semantic" for r in _stats_for(session, user_id))
        from sqlalchemy import select

        from psych_support_bot.infra.db.models import ProfileInterventionEvent

        answered = list(
            session.scalars(
                select(ProfileInterventionEvent).where(
                    ProfileInterventionEvent.user_id == user_id,
                    ProfileInterventionEvent.intervention_kind == "question_answered",
                )
            )
        )
        assert len(answered) == 1 and '"verdict": "confirm"' in answered[0].detail_json


def test_verification_deny_rejects_and_never_resurrects(monkeypatch) -> None:
    user_id = _uid()
    session_id = f"s-{uuid4().hex[:8]}"
    _seed_verification_scenario(user_id, session_id)
    monkeypatch.setattr(get_settings(), "profile_llm_extraction_enabled", True)
    monkeypatch.setattr(
        "psych_support_bot.ai.profile.semantic.generate_profile_extraction",
        lambda **kwargs: '{"verdict": "deny", "reason_zh": "用户明确否认"}',
    )
    _save_user_message(session_id, "不是这样的，我睡眠还行")
    _run(user_id, session_id, topics=["sleep"], exercise_tag=None, valence_text="不是这样的，我睡眠还行", turn_count=3)
    with SessionLocal() as session:
        belief = get_belief(session, user_id, "sleep")
        assert belief.status == "rejected"  # D8 负记忆，永不复活


def test_verification_unclear_keeps_l4(monkeypatch) -> None:
    user_id = _uid()
    session_id = f"s-{uuid4().hex[:8]}"
    _seed_verification_scenario(user_id, session_id)
    monkeypatch.setattr(get_settings(), "profile_llm_extraction_enabled", True)
    monkeypatch.setattr(
        "psych_support_bot.ai.profile.semantic.generate_profile_extraction",
        lambda **kwargs: "我想聊聊别的事情",  # 畸形输出 → unclear
    )
    _save_user_message(session_id, "这个不好说")
    _run(user_id, session_id, topics=[], exercise_tag=None, valence_text="这个不好说", turn_count=3)
    with SessionLocal() as session:
        belief = get_belief(session, user_id, "sleep")
        assert belief.layer == "L4" and belief.status == "active"


def test_verification_skipped_when_no_pending_or_stale(monkeypatch) -> None:
    user_id = _uid()
    session_id = f"s-{uuid4().hex[:8]}"
    # 无注入 → 不判定（回半环不触发，无 llm_verification 行）
    _run(user_id, session_id, topics=[], exercise_tag=None, valence_text="随便聊聊", turn_count=99)
    with SessionLocal() as session:
        assert all(r.trigger != "llm_verification" for r in _stats_for(session, user_id))

    # 出窗注入（>3 条用户消息）→ 迟到的回答不判定
    user_id2 = _uid()
    session_id2 = f"s-{uuid4().hex[:8]}"
    _seed_verification_scenario(user_id2, session_id2)

    from psych_support_bot.infra.db.models import Message

    with SessionLocal() as session:
        for offset in (1, 2, 3, 4):
            session.add(
                Message(
                    session_id=session_id2,
                    role="user",
                    content=f"岔开话题 {offset}",
                    created_at=_now_plus(offset),
                )
            )
        session.commit()
    monkeypatch.setattr(get_settings(), "profile_llm_extraction_enabled", True)
    monkeypatch.setattr(
        "psych_support_bot.ai.profile.semantic.generate_profile_extraction",
        lambda **kwargs: '{"verdict": "confirm"}',
    )
    _run(user_id2, session_id2, topics=[], exercise_tag=None, valence_text="其实我睡得还行", turn_count=99)
    with SessionLocal() as session:
        assert all(r.trigger != "llm_verification" for r in _stats_for(session, user_id2))
        assert get_belief(session, user_id2, "sleep").layer == "L4"


def test_verification_accepts_late_reply_within_window(monkeypatch) -> None:
    """窗内迟到表态（绕了 2 轮再回应）仍可判定——V2 否认路径线上复现的修复。"""
    user_id = _uid()
    session_id = f"s-{uuid4().hex[:8]}"
    _seed_verification_scenario(user_id, session_id)
    from psych_support_bot.infra.db.models import Message

    with SessionLocal() as session:
        for offset in (1, 2):
            session.add(
                Message(
                    session_id=session_id,
                    role="user",
                    content=f"先聊点别的 {offset}",
                    created_at=_now_plus(offset),
                )
            )
        session.commit()
    monkeypatch.setattr(get_settings(), "profile_llm_extraction_enabled", True)
    monkeypatch.setattr(
        "psych_support_bot.ai.profile.semantic.generate_profile_extraction",
        lambda **kwargs: '{"verdict": "deny"}',
    )
    _run(user_id, session_id, topics=[], exercise_tag=None, valence_text="其实不是这样的", turn_count=99)
    with SessionLocal() as session:
        assert any(r.trigger == "llm_verification" for r in _stats_for(session, user_id))
        assert get_belief(session, user_id, "sleep").status == "rejected"  # 迟到否认也入 D8


def _now_plus(days: int):
    from datetime import UTC, datetime, timedelta

    return datetime.now(UTC) + timedelta(days=days)


# --- 质询闭环 ---


def test_question_candidates_mechanism_tier_outranks_topic() -> None:
    """调参 A 回归：跨水位后，D3 机制优先于更高置信度的 D1 主题占用提问预算。"""
    user_id = _uid()
    with SessionLocal() as session:
        from psych_support_bot.infra.db.profile_repositories import list_question_candidates, record_claim

        record_claim(session, user_id, dimension="D1", key="social_anxiety", claim_text="x", confidence=0.98)
        record_claim(
            session, user_id, dimension="D3", key="avoidance_maintenance.social", claim_text="x", confidence=0.72
        )
        session.commit()
        top = list_question_candidates(session, user_id)
        assert [b.key for b in top] == ["avoidance_maintenance.social"]


def test_semantic_prompt_has_mechanism_few_shot() -> None:
    from psych_support_bot.ai.profile.semantic import _SYSTEM_PROMPT

    assert "Mechanism priority" in _SYSTEM_PROMPT
    assert "avoidance_maintenance.social" in _SYSTEM_PROMPT  # few-shot 例句钉住


def _state(**overrides) -> dict:
    state = {
        "user_id": "u",
        "session_id": "s",
        "user_message": "最近很累",
        "memory_summary": "",
        "user_history_text": "",
        "recent_risk_level": "",
        "knowledge_context": "",
        "mode": "support",
        "risk_result": RiskResult(risk_level="low", risk_types=[], needs_crisis_mode=False, reason=""),
        "generated_reply": None,
        "session_summary": "",
        "topics": [],
        "fallback_used": False,
        "consultation_required": False,
        "consultation_agents": [],
        "consultation_notes": "",
        "consultation_opinions": [],
        "interview_stage": "engagement",
        "question_strategy": "open",
        "challenge_allowed": True,
        "loop_hint": "base hint",
        "exercise_history": [],
        "refusal_history": [],
        "expected_language": "zh",
        "turn_count": 1,
        "safety_floor_risk_level": "",
        "no_question_mode": False,
        "profile_question_candidates": ["睡不好、睡不安稳"],
        "last_bot_reply": "",
        "speculative_reply": None,
        "emotional_state": "",
        "llm_topics": [],
        "recent_history": [],
    }
    state.update(overrides)
    return state


def test_question_candidate_injected_into_loop_hint() -> None:
    result = plan_consultation(_state())
    assert "gently verify" in result["loop_hint"]
    assert "睡不好、睡不安稳" in result["loop_hint"]
    # plan_consultation 会用 interview_process 重建 loop_hint——注入是前置
    # 追加，最终 hint 同时包含质询内容与阶段指导。
    assert len(result["loop_hint"]) > len("gently verify")


def test_question_candidate_gated_by_no_question_and_crisis() -> None:
    result = plan_consultation(_state(no_question_mode=True))
    assert "gently verify" not in result["loop_hint"]
    crisis = _state(mode="crisis")
    crisis["risk_result"] = RiskResult(risk_level="high", risk_types=[], needs_crisis_mode=True, reason="")
    assert "gently verify" not in plan_consultation(crisis)["loop_hint"]
    assert "gently verify" not in plan_consultation(_state(profile_question_candidates=[]))["loop_hint"]


# --- 质询候选水位 ---


def test_question_candidates_threshold() -> None:
    from psych_support_bot.infra.db.profile_repositories import list_question_candidates

    user_id = _uid()
    with SessionLocal() as session:
        from psych_support_bot.infra.db.profile_repositories import record_claim

        record_claim(session, user_id, dimension="D1", key="sleep", claim_text="x", confidence=0.75)
        record_claim(session, user_id, dimension="D1", key="grief", claim_text="x", confidence=0.6)
        session.commit()
        candidates = list_question_candidates(session, user_id)
        assert [b.key for b in candidates] == ["sleep"]  # 0.75 ≥ 0.7 水位；0.6 不足
