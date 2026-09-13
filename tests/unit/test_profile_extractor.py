"""画像轮次提取器（K1b）单测。

覆盖：
1. build_turn_claims：危机轮零提取 / D4 优先保留 / 总量 ≤3 / topics 截断。
2. scan_valence：负向词优先（"没什么用"含"有用"子串的陷阱）/ 中性基线。
3. run_turn_extraction 端到端（真实测试库）：belief L4 落库 + 证据链 +
   P2 统计行 claims_out + D4 效果值更新（neutral→worked 学习信号）。
4. fail-open：开关关闭不写任何行；提取异常不外抛且留 error 统计。
"""

from uuid import uuid4

import pytest
from sqlalchemy import select

from psych_support_bot.ai.profile.extractor import (
    build_turn_claims,
    run_turn_extraction,
    scan_valence,
)
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.models import ProfileExtractionStats
from psych_support_bot.infra.db.profile_repositories import (
    get_belief,
    get_belief_events,
)
from psych_support_bot.infra.db.session import SessionLocal


def _uid() -> str:
    return f"pe-{uuid4().hex[:8]}"


def _stats_for(session, user_id: str) -> list[ProfileExtractionStats]:
    stmt = select(ProfileExtractionStats).where(ProfileExtractionStats.user_id == user_id)
    return list(session.scalars(stmt))


def _run(user_id: str, session_id: str, *, topics=None, risk_level="low", exercise_tag=None, valence_text=""):
    with SessionLocal() as session:
        run_turn_extraction(
            session,
            user_id=user_id,
            session_id=session_id,
            topics=list(topics or []),
            risk_level=risk_level,
            exercise_tag=exercise_tag,
            valence_text=valence_text,
        )


# --- build_turn_claims（纯函数） ---


def test_crisis_turn_produces_zero_claims() -> None:
    for level in ("high", "critical"):
        claims = build_turn_claims(
            topics=["sleep", "anxiety"], risk_level=level, exercise_tag="dbt_tipp", valence_text="有用"
        )
        assert claims == []


def test_practice_turn_suppresses_d1_topics() -> None:
    # 完成叙述里的情绪词是练习使用的情境，不是当期话题陈述（fixture 金标准）：
    # 练习轮只产出 D4，同轮主题词不再产出 D1。
    claims = build_turn_claims(
        topics=["sleep", "anxiety", "stress"],
        risk_level="low",
        exercise_tag="dbt_tipp",
        valence_text="昨晚心慌，做完舒服多了",
    )
    assert [(c.dimension, c.key) for c in claims] == [("D4", "dbt_tipp")]


def test_assessment_context_suppresses_d1_topics() -> None:
    # "焦虑量表没测准"里的"焦虑"是测评行为的组成部分——行为层信号不进内容层
    claims = build_turn_claims(
        topics=["anxiety"],
        risk_level="low",
        exercise_tag=None,
        valence_text="刚做过那个焦虑量表，总觉得没测准，想再测一次",
    )
    assert claims == []


def test_no_exercise_turn_caps_d1_at_three() -> None:
    claims = build_turn_claims(
        topics=["sleep", "anxiety", "stress", "burnout"],
        risk_level="low",
        exercise_tag=None,
        valence_text="",
    )
    assert len(claims) == 3  # topics 截断到总量上限
    assert {c.key for c in claims} == {"sleep", "anxiety", "stress"}


def test_topics_only_turn_fills_d1() -> None:
    claims = build_turn_claims(topics=["grief"], risk_level="low", exercise_tag=None, valence_text="")
    assert len(claims) == 1
    assert claims[0].dimension == "D1"
    assert claims[0].confidence == 0.4


# --- scan_valence ---


def test_valence_aversive_takes_priority_over_worked_substring() -> None:
    # 陷阱句："没什么用"包含"有用"子串，负向词必须先判
    assert scan_valence("做了但是没什么用") == "aversive"
    assert scan_valence("试了两次，更焦虑了") == "aversive"
    assert scan_valence("做完之后真的有用") == "worked"
    assert scan_valence("it didn't help at all") == "aversive"
    assert scan_valence("this really helped") == "worked"
    assert scan_valence("我做完了") == "neutral"


# --- run_turn_extraction（端到端） ---


def test_turn_extraction_persists_beliefs_stats_and_evidence() -> None:
    user_id = _uid()
    session_id = f"s-{uuid4().hex[:8]}"
    # 第一轮：练习完成（D4，同轮主题词被抑制）
    _run(user_id, session_id, topics=["sleep"], exercise_tag="dbt_tipp", valence_text="这次呼吸练习挺有用")
    # 第二轮：纯主题轮（D1 sleep）
    _run(user_id, f"s-{uuid4().hex[:8]}", topics=["sleep"], exercise_tag=None, valence_text="")
    with SessionLocal() as session:
        belief = get_belief(session, user_id, "sleep")
        assert belief is not None and belief.layer == "L4" and belief.status == "active"
        assert belief.evidence_json  # 证据链已落
        assert belief.confidence == pytest.approx(0.4)  # 练习轮的 sleep 主题被抑制，仅第二轮独立创建
        d4 = get_belief(session, user_id, "dbt_tipp")
        assert d4 is not None
        assert d4.value_json == '{"tag": "dbt_tipp", "effect": "worked"}'
        events = [e.event_type for e in get_belief_events(session, user_id, d4.id)]
        assert events[0] == "value_updated"  # 最新在前：效果值更新是最后一笔
        assert "created" in events
        # P2 统计：两轮各一行
        rows = _stats_for(session, user_id)
        assert len(rows) == 2
        assert {r.trigger for r in rows} == {"practice_event", "topic_flow"}
        assert {r.claims_out for r in rows} == {1}
        assert {r.status for r in rows} == {"ok"}


def test_valence_transition_updates_value_over_turns() -> None:
    user_id = _uid()
    _run(user_id, f"s-{uuid4().hex[:8]}", exercise_tag="panic_grounding_5_4_3_2_1", valence_text="做完了")
    _run(user_id, f"s-{uuid4().hex[:8]}", exercise_tag="panic_grounding_5_4_3_2_1", valence_text="这次做完轻松多了")
    with SessionLocal() as session:
        belief = get_belief(session, user_id, "panic_grounding_5_4_3_2_1")
        assert belief is not None
        assert '"effect": "worked"' in belief.value_json  # neutral → worked 学习信号已更新
        # 同 key 单行：confidence 随第二轮 support 推进而非新建
        assert belief.confidence > 0.3


def test_crisis_turn_records_stats_but_no_beliefs() -> None:
    user_id = _uid()
    _run(user_id, f"s-{uuid4().hex[:8]}", topics=["sleep"], risk_level="high")
    with SessionLocal() as session:
        assert get_belief(session, user_id, "sleep") is None
        rows = _stats_for(session, user_id)
        assert len(rows) == 1 and rows[0].claims_out == 0 and rows[0].trigger == "crisis_guard"


def test_disabled_flag_writes_nothing(monkeypatch) -> None:
    user_id = _uid()
    monkeypatch.setattr(get_settings(), "profile_extraction_enabled", False)
    _run(user_id, f"s-{uuid4().hex[:8]}", topics=["sleep"])
    with SessionLocal() as session:
        assert get_belief(session, user_id, "sleep") is None
        assert _stats_for(session, user_id) == []


def test_extraction_failure_is_swallowed_and_recorded(monkeypatch) -> None:
    user_id = _uid()
    session_id = f"s-{uuid4().hex[:8]}"
    # 模拟写入层故障：record_claim 抛错 → fail-open 吞掉 + error 统计留痕
    import psych_support_bot.ai.profile.extractor as extractor_module

    def _boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(extractor_module, "record_claim", _boom)
    with SessionLocal() as session:
        run_turn_extraction(
            session,
            user_id=user_id,
            session_id=session_id,
            topics=["sleep"],
            risk_level="low",
            exercise_tag=None,
            valence_text="",
        )
        rows = _stats_for(session, user_id)
        assert len(rows) == 1 and rows[0].status == "error"
        assert get_belief(session, user_id, "sleep") is None
