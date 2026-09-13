"""画像渲染器（K1c）单测。

覆盖：
1. compute_profile_budget：零压舒张 / 满压收缩 / floor-cap 钳制。
2. 整条装箱：预算硬上限、无残句截断、D8 固定首槽豁免。
3. 维度规则：D3 未认领不出 / L4 过水位才出 / 查无 label 即跳（key 不泄漏）。
4. 快照接线：build_memory_snapshot 注入轮次上下文后画像层出现且既有层不受影响。
"""

from uuid import uuid4

from psych_support_bot.ai.profile.renderer import (
    compute_profile_budget,
    render_profile_block,
)
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.profile_repositories import (
    get_belief,
    record_claim,
    reject_belief,
)
from psych_support_bot.infra.db.repositories import build_memory_snapshot, upsert_user_profile
from psych_support_bot.infra.db.session import SessionLocal


def _uid() -> str:
    return f"pr-{uuid4().hex[:8]}"


def _seed(user_id: str, *, key: str, dimension: str, confidence: float = 0.6, **kwargs):
    with SessionLocal() as session:
        belief, _ = record_claim(
            session, user_id, dimension=dimension, key=key, claim_text="测试", confidence=confidence, **kwargs
        )
        session.commit()
        return belief.id


def _render(
    user_id: str, *, language: str = "", user_message: str = "", recent_risk_level: str = "", tail_load: int = 0
):
    with SessionLocal() as session:
        return render_profile_block(
            session,
            user_id,
            language=language,
            user_message=user_message,
            recent_risk_level=recent_risk_level,
            tail_load=tail_load,
        )


# --- 动态预算 ---


def test_budget_expands_at_zero_pressure_and_shrinks_at_full() -> None:
    base, floor, cap = 480, 160, 720
    relaxed = compute_profile_budget(0, 0.0, base=base, floor=floor, cap=cap)
    squeezed = compute_profile_budget(9999, 1.0, base=base, floor=floor, cap=cap)
    assert relaxed == round(base * 1.2)
    assert squeezed == round(base * 0.8)
    assert floor <= squeezed < relaxed <= cap


def test_budget_clamped_by_floor_and_cap() -> None:
    assert compute_profile_budget(0, 0.0, base=100, floor=160, cap=720) == 160
    assert compute_profile_budget(0, 0.0, base=5000, floor=160, cap=720) == 720


# --- 渲染规则（端到端，真实测试库） ---


def test_d8_fixed_first_and_l4_threshold_gating() -> None:
    user_id = _uid()
    # L4 弱信号（0.4 < 0.55 水位）不应出现
    _seed(user_id, key="grief", dimension="D1", confidence=0.4)
    # 达到水位（≥0.55）的 D1 出现
    _seed(user_id, key="sleep", dimension="D1", confidence=0.6)
    # 被否决信念进 D8 首槽
    _seed(user_id, key="relationships", dimension="D1", confidence=0.9)
    with SessionLocal() as session:
        relationships = get_belief(session, user_id, "relationships")
        reject_belief(session, user_id, relationships.id)
        session.commit()

    block = _render(user_id, user_message="最近睡得很差")
    assert block is not None
    assert block.index("应回避") == 0  # D8 固定首槽
    assert "睡不好" in block  # sleep 标签（达水位 + 本轮主题命中）
    # 弱信号 grief（0.4 < 水位）不渲染
    assert "失去重要的人" not in block.replace("应回避（用户已明确搁置）：", "")


def test_unconfirmed_d3_never_renders_but_confirmed_does() -> None:
    user_id = _uid()
    _seed(user_id, key="control_struggle", dimension="D3", confidence=0.9)  # extracted → 强制 L4 且未认领
    _seed(user_id, key="rumination_loop", dimension="D3", confidence=0.9, source="user_confirmed", layer="L2")
    block = _render(user_id)
    assert block is not None
    assert "一遍遍回放" in block  # 已认领
    assert "总想先把它赶走" not in block  # 未认领


def test_d4_renders_exercise_name_with_effect_phrase() -> None:
    user_id = _uid()
    _seed(user_id, key="dbt_tipp", dimension="D4", confidence=0.6)
    with SessionLocal() as session:
        from psych_support_bot.infra.db.profile_repositories import update_belief_value

        update_belief_value(session, user_id, "dbt_tipp", {"tag": "dbt_tipp", "effect": "worked"})
        session.commit()
    block = _render(user_id)
    assert block is not None
    assert "DBT TIPP" in block and "用起来有帮助" in block


def test_unknown_key_skipped_no_leak() -> None:
    user_id = _uid()
    _seed(user_id, key="mystery_free_text_key", dimension="D5", confidence=0.9)
    block = _render(user_id)
    assert block is None or "mystery_free_text_key" not in block


def test_packing_respects_budget_without_partial_items() -> None:
    user_id = _uid()
    for index, topic in enumerate(["anxiety", "sleep", "depression", "burnout", "grief", "anger"]):
        _seed(user_id, key=topic, dimension="D1", confidence=0.6)
        with SessionLocal() as session:
            belief = get_belief(session, user_id, topic)
            belief.last_evidence_at = belief.last_evidence_at.replace(microsecond=index)
            session.commit()
    with SessionLocal() as session:
        block = render_profile_block(
            session,
            user_id,
            language="",
            user_message="又焦虑又睡不着",
            tail_load=5000,  # 高压 → 预算收缩到 0.8×BASE
        )
    settings = get_settings()
    budget_floor = settings.profile_render_floor
    assert block is not None
    assert len(block) <= max(settings.profile_render_base, budget_floor)  # cap 硬上限
    assert "…" not in block  # 整条装箱，无残句


# --- 快照接线 ---


def test_snapshot_includes_profile_layer_and_keeps_existing_layers() -> None:
    user_id = _uid()
    _seed(user_id, key="sleep", dimension="D1", confidence=0.6)
    with SessionLocal() as session:
        upsert_user_profile(session, user_id, "小测", "睡眠不好", "", "", "")  # 手填画像段 → 快照多段拼接
        session.commit()
        snapshot = build_memory_snapshot(
            session,
            user_id,
            language="zh",
            user_message="最近睡不着",
            recent_risk_level="low",
        )
    assert "睡不好" in snapshot  # 画像层
    assert " || " in snapshot  # 与手填画像段共存：既有快照拼接结构不变
