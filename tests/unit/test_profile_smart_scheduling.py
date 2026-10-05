"""智能调度单测：价值驱动提取 / 活性排序 / 质询确认率反馈。

三块改进互不影响原有红线契约：
- 价值门只决定"本轮是否发起 LLM 提取"，危机门控在调用方仍先生效；
- 活性只决定渲染排序，L4 水位 / D3 未认领门控仍看原始字段；
- 确认率权重无历史时恒为 1.0，静态分级（D3>D5>其余）原样回退。
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from psych_support_bot.ai.profile.renderer import belief_activity, render_profile_block
from psych_support_bot.ai.profile.semantic import (
    _should_llm_extract,
    compute_extraction_value,
    detect_mechanism_keys,
)
from psych_support_bot.infra.db.profile_repositories import (
    _question_confirm_rate_weight,
    list_question_candidates,
    record_claim,
    record_intervention_event,
)
from psych_support_bot.infra.db.session import SessionLocal


def _uid() -> str:
    return f"ss-{uuid4().hex[:8]}"


def _seed(user_id: str, *, key: str, dimension: str, confidence: float = 0.6, **kwargs):
    with SessionLocal() as session:
        belief, _ = record_claim(
            session, user_id, dimension=dimension, key=key, claim_text="测试", confidence=confidence, **kwargs
        )
        session.commit()
        return belief.id


def _answer(user_id: str, belief_key: str, verdict: str, *, session_id: str = "s1") -> None:
    with SessionLocal() as session:
        record_intervention_event(
            session,
            user_id=user_id,
            session_id=session_id,
            kind="question_answered",
            detail={"verdict": verdict, "belief_key": belief_key},
        )
        session.commit()


# --- 方案 1：边际信息增益纯函数 ---


def test_extraction_value_components() -> None:
    # 新用户 + 单主题：新颖度 0.45 + 维度稀缺 0.25 = 0.70（中间带，交节流裁决）
    assert compute_extraction_value(topic_keys=["sleep"], mechanism_keys=[], active_beliefs=[]) == 0.70
    # dict 入参：维度稀缺按 0 计；主题已成熟覆盖 → 价值归零（抑制区）
    assert compute_extraction_value(topic_keys=["sleep"], mechanism_keys=[], active_beliefs={"sleep": 0.9}) == 0.0
    # 新用户 + 新颖机制信号：稀缺 0.25 + 机制 0.30 = 0.55
    val = compute_extraction_value(topic_keys=[], mechanism_keys=["rumination_loop"], active_beliefs=[])
    assert val == 0.55
    # 机制信念已成熟 → 机制分量归零
    assert (
        compute_extraction_value(
            topic_keys=[],
            mechanism_keys=["rumination_loop"],
            active_beliefs={"rumination_loop": 0.8},
        )
        == 0.0
    )
    # 部分新颖：两个主题只覆盖一个 → 新颖度折半
    val = compute_extraction_value(topic_keys=["sleep", "anxiety"], mechanism_keys=[], active_beliefs={"sleep": 0.9})
    assert val == round(0.45 * 0.5, 4)


def test_detect_mechanism_keys_hits_d3_d5_but_never_distortion() -> None:
    hits = detect_mechanism_keys("我一直在回放到底哪里做错了，万一再发生一次怎么办")
    assert "rumination_loop" in hits
    # D5 动机锚同样可命中
    assert "waiting_for_readiness" in detect_mechanism_keys("等我自信了我就去做")
    # 认知歪曲词只允许 identify-only，绝不作为机制提取触发器
    assert detect_mechanism_keys("我就是个废物") == []
    assert detect_mechanism_keys("今天随便聊聊天") == []


# --- 方案 1：价值门（真实测试库） ---


def test_gate_suppresses_when_all_topics_mature(monkeypatch) -> None:
    """成熟用户重复命中已覆盖主题、无新机制 → 即使主题≥2 也抑制本轮提取。"""
    from psych_support_bot.infra.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "profile_llm_extraction_enabled", True)
    user_id = _uid()
    _seed(user_id, key="sleep", dimension="D1", confidence=0.6)
    _seed(user_id, key="anxiety", dimension="D1", confidence=0.6)
    with SessionLocal() as session:
        # 基础规则本应 True（主题≥2），价值低门将其抑制
        assert not _should_llm_extract(
            "我焦虑睡不着",
            practice_event=False,
            turn_count=2,
            session=session,
            user_id=user_id,
        )


def test_gate_novel_mechanism_triggers_early(monkeypatch) -> None:
    """新颖机制信号打破每 N 轮节流：turn 2 + 无主题也提前提取。"""
    from psych_support_bot.infra.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "profile_llm_extraction_enabled", True)
    user_id = _uid()
    with SessionLocal() as session:
        text = "开会前我提前把要说的话在脑子里排练了好多遍，能不说话就不说话"
        assert _should_llm_extract(
            text,
            practice_event=False,
            turn_count=2,  # 非节流轮
            session=session,
            user_id=user_id,
        )


def test_gate_cold_start_single_topic_follows_cadence() -> None:
    """新用户单主题、无机制：走基础节流——turn 2 不提，turn 3 提。"""
    user_id = _uid()
    with SessionLocal() as session:
        assert not _should_llm_extract("有点焦虑", practice_event=False, turn_count=2, session=session, user_id=user_id)
        assert _should_llm_extract("有点焦虑", practice_event=False, turn_count=3, session=session, user_id=user_id)


def test_gate_without_session_keeps_legacy_rules() -> None:
    """无 session（冷路径/直接调用）行为与改造前完全一致。"""
    assert _should_llm_extract("我焦虑睡不着", practice_event=False, turn_count=1)
    assert not _should_llm_extract("我做完了", practice_event=False, turn_count=1)


# --- 方案 3：活性排序 ---


def _belief(layer: str, confidence: float, age_days: float) -> SimpleNamespace:
    return SimpleNamespace(
        layer=layer,
        confidence=confidence,
        last_evidence_at=datetime.now(UTC) - timedelta(days=age_days),
    )


def test_belief_activity_decay_and_layer_factors() -> None:
    now = datetime.now(UTC)
    fresh_l4 = _belief("L4", 0.9, 0.0)
    assert belief_activity(fresh_l4, now=now) == 0.63  # 0.9 × 1.0 × 0.7
    # 两个半衰期（120 天）→ 衰减到 1/4
    stale_l4 = _belief("L4", 0.9, 120.0)
    assert abs(belief_activity(stale_l4, now=now) - 0.9 * 0.25 * 0.7) < 1e-6
    # L2 已认领不打层折让
    fresh_l2 = _belief("L2", 0.9, 0.0)
    assert belief_activity(fresh_l2, now=now) == 0.9
    # 已认领的中等置信信念（0.6）活性高于待验证的 0.7
    assert belief_activity(_belief("L2", 0.6, 0.0), now=now) > belief_activity(_belief("L4", 0.7, 0.0), now=now)


def test_renderer_fresh_belief_ranks_before_stale() -> None:
    """同为 L4 D1、无本轮主题偏向时，新证据信念排在旧信念前面。"""
    user_id = _uid()
    old_id = _seed(user_id, key="grief", dimension="D1", confidence=0.9)
    _seed(user_id, key="anger", dimension="D1", confidence=0.9)
    old_time = datetime.now(UTC) - timedelta(days=150)
    with SessionLocal() as session:
        from psych_support_bot.infra.db.models import ProfileBelief

        session.get(ProfileBelief, old_id).last_evidence_at = old_time.replace(tzinfo=None)
        session.commit()
    block = _render(user_id)
    assert block is not None
    assert block.index("上火") < block.index("失去重要的人")


def _render(user_id: str, *, user_message: str = ""):
    with SessionLocal() as session:
        return render_profile_block(session, user_id, language="zh", user_message=user_message)


# --- 方案 4：质询确认率反馈 ---


def test_confirm_rate_weight_neutral_when_no_history() -> None:
    assert _question_confirm_rate_weight(0.5) == 1.0
    assert _question_confirm_rate_weight(0.0) == 0.6
    assert _question_confirm_rate_weight(1.0) == 1.4


def test_d5_with_strong_confirm_history_outranks_denied_d3() -> None:
    """D5 连续被确认 → 权重 1.4；D3 连续被否认 → 权重 0.6 区间，D5 反超。"""
    user_id = _uid()
    _seed(user_id, key="avoidance_maintenance.social", dimension="D3", confidence=0.72)
    _seed(user_id, key="waiting_for_readiness", dimension="D5", confidence=0.72)
    for _ in range(3):
        _answer(user_id, "avoidance_maintenance.social", "deny")
    for _ in range(3):
        _answer(user_id, "waiting_for_readiness", "confirm")
    with SessionLocal() as session:
        top = list_question_candidates(session, user_id, limit=2)
    assert next(b.key for b in top) == "waiting_for_readiness"


def test_denied_d3_still_outranks_confirmed_d1() -> None:
    """ADR 边界：再差的 D3（权重下限 0.6 → 1.8）也不低于最好的 D1（1.4）。"""
    user_id = _uid()
    _seed(user_id, key="social_anxiety", dimension="D1", confidence=0.98)
    _seed(user_id, key="avoidance_maintenance.social", dimension="D3", confidence=0.72)
    for _ in range(3):
        _answer(user_id, "avoidance_maintenance.social", "deny")
    for _ in range(3):
        _answer(user_id, "social_anxiety", "confirm")
    with SessionLocal() as session:
        top = list_question_candidates(session, user_id, limit=2)
    assert next(b.key for b in top) == "avoidance_maintenance.social"


def test_no_history_falls_back_to_static_tiers() -> None:
    """无质询历史：D3 > D5 > D1 静态分级原样生效。"""
    user_id = _uid()
    _seed(user_id, key="social_anxiety", dimension="D1", confidence=0.98)
    _seed(user_id, key="waiting_for_readiness", dimension="D5", confidence=0.72)
    _seed(user_id, key="avoidance_maintenance.social", dimension="D3", confidence=0.72)
    with SessionLocal() as session:
        top = list_question_candidates(session, user_id, limit=3)
    assert [b.key for b in top] == [
        "avoidance_maintenance.social",
        "waiting_for_readiness",
        "social_anxiety",
    ]
