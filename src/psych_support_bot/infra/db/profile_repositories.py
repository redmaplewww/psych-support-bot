"""画像 belief 仓储层。

合并策略（K1a 确定性版，K2 质询闭环再调参）：
- 同 (user_id, key) 只有一行当前状态；relation=supports 推进置信度并
  追加证据，relation=contradicts 衰减置信度（L2 同时降级回 L4）。
- 否决（reject）后同 key 永不复活：再收到 claim 只返回守卫标记，
  不新建行、不记事件（D8 负记忆）。
- 结构性红线在写入入口钳制：extracted 来源强制 L4；distortion.* key
  直接 ValueError（认知歪曲禁止沉淀，PROFILE_DECISIONS / 知识提炼 §7.2）。

查询全部走 SQLAlchemy 2.0 参数化构造（绑定参数，无字符串拼接）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from psych_support_bot.infra.db.models import (
    ProfileBelief,
    ProfileBeliefEvent,
    ProfileExtractionStats,
    ProfileInterventionEvent,
    ProfileMemoryPreference,
    UserTimeProfile,
    utcnow,
)

logger = logging.getLogger(__name__)

# belief 行上只保留最近 N 条证据 message id；完整史在事件表。
from psych_support_bot.ai.profile.constants import (
    CONFIDENCE_CEILING,
    L4_QUESTION_THRESHOLD,
    MAX_EVIDENCE_REFS,
    SUPPORT_GAIN,
)

# 向后兼容：旧代码可能直接引用 CONTRADICT_FACTOR。
CONTRADICT_FACTOR = 0.5

_VALID_DIMENSIONS = {f"D{i}" for i in range(1, 9)}


def compute_confidence_weight(
    belief: ProfileBelief,
    *,
    support_count: int = 0,
    contradict_count: int = 0,
    context_diversity: int = 0,
    days_since_last_evidence: float = 0.0,
) -> float:
    """8 维置信度加权因子（0.5-1.0），用于调制 SUPPORT_GAIN 的增益幅度。

    设计：base = 1.0（全额增益），负面信号逐项扣减。
    来源可靠、情境多样、时新、无矛盾的证据获得全额增益；
    反之衰减，但最低 0.5（保留基本增益，避免新信念停滞）。
    """
    weight = 1.0
    # 1. 来源可信度（user_confirmed 不扣；extracted 扣 0.1）
    weight -= {"user_confirmed": 0.0, "user_stated": 0.05, "program": 0.05, "extracted": 0.1}.get(belief.source, 0.1)
    # 2. 确认层级（L2 不扣；L4 扣 0.1）
    weight -= {"L1": 0.05, "L2": 0.0, "L3": 0.1, "L4": 0.1}.get(belief.layer, 0.1)
    # 3. 证据积累（0 条扣 0.15；3+ 条不扣）
    if support_count < 3:
        weight -= 0.15 * (1 - support_count / 3)
    # 4. 情境多样性（仅在有足够证据时评估；新信念不惩罚）
    if support_count >= 2 and context_diversity < 3:
        weight -= 0.1 * (1 - context_diversity / 3)
    # 5. 时新性（30+ 天扣 0.15；7 天内不扣）
    if days_since_last_evidence > 7:
        weight -= min(0.15, 0.05 * (days_since_last_evidence - 7) / 23)
    # 6. 矛盾惩罚（每次扣 0.1）
    weight -= 0.1 * contradict_count
    # 7. needs_clarification 额外扣 0.1
    try:
        val = json.loads(belief.value_json or "{}")
        if val.get("clarification_status") == "needs_clarification":
            weight -= 0.1
    except (TypeError, ValueError):
        pass
    return round(min(1.0, max(0.5, weight)), 4)


def is_profile_memory_enabled(session: Session, user_id: str) -> bool:
    """Return the user's profile-memory choice; missing rows preserve legacy-on behavior."""
    preference = session.get(ProfileMemoryPreference, user_id)
    return preference.enabled if preference is not None else True


def is_profile_beta_accepted(session: Session, user_id: str) -> bool:
    """画像 Beta 知悉协议是否已确认。缺少行 = 未确认。"""
    from psych_support_bot.infra.db.models import ProfileBetaConsent

    return session.get(ProfileBetaConsent, user_id) is not None


def is_sensitive_background_enabled(session: Session, user_id: str) -> bool:
    """敏感背景提取是否被用户授权。缺少行或未确认 Beta = 未授权。"""
    from psych_support_bot.infra.db.models import ProfileBetaConsent

    consent = session.get(ProfileBetaConsent, user_id)
    return consent is not None and consent.sensitive_background_enabled


def set_profile_memory_enabled(session: Session, user_id: str, enabled: bool) -> ProfileMemoryPreference:
    preference = session.get(ProfileMemoryPreference, user_id)
    if preference is None:
        preference = ProfileMemoryPreference(user_id=user_id)
        session.add(preference)
    preference.enabled = enabled
    preference.disabled_at = None if enabled else utcnow()
    preference.updated_at = utcnow()
    session.flush()
    return preference


def has_profile_memory_data(session: Session, user_id: str) -> bool:
    """Whether any inferred profile, profile audit, or time-profile data remains."""
    models = (ProfileBelief, ProfileBeliefEvent, ProfileExtractionStats, ProfileInterventionEvent, UserTimeProfile)
    if any(session.query(model).filter(model.user_id == user_id).first() is not None for model in models):
        return True
    # 也检查 UserProfile 上的画像扩展字段。
    from psych_support_bot.infra.db.models import ProfileEvolutionJob, ProfileSnapshot, UserProfile

    profile = session.get(UserProfile, user_id)
    if profile and profile.background_json and profile.background_json != "{}":
        return True
    if profile and profile.understanding_json and profile.understanding_json != "{}":
        return True
    # 检查异步任务和快照。
    if session.query(ProfileEvolutionJob).filter(ProfileEvolutionJob.user_id == user_id).first() is not None:
        return True
    return session.query(ProfileSnapshot).filter(ProfileSnapshot.user_id == user_id).first() is not None


def record_extraction_stats(
    session: Session,
    user_id: str,
    *,
    session_id: str | None = None,
    trigger: str = "",
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    claims_out: int = 0,
    status: str = "ok",
    error: str = "",
) -> ProfileExtractionStats:
    """P2 全量统计：每次提取调用（含熔断跳过 status="skipped"）一行。"""
    row = ProfileExtractionStats(
        user_id=user_id,
        session_id=session_id,
        trigger=trigger,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        claims_out=claims_out,
        status=status,
        error=error,
    )
    session.add(row)
    session.flush()
    return row


def _append_event(
    session: Session,
    belief: ProfileBelief,
    event_type: str,
    *,
    evidence: list[int],
    detail: dict | None = None,
    stats_id: int | None = None,
) -> None:
    session.add(
        ProfileBeliefEvent(
            belief_id=belief.id,
            user_id=belief.user_id,
            event_type=event_type,
            evidence_json=json.dumps(evidence),
            detail_json=json.dumps(detail or {}, ensure_ascii=False),
            origin_stats_id=stats_id,
        )
    )


def get_belief(session: Session, user_id: str, key: str) -> ProfileBelief | None:
    """同 key 的当前行（含 rejected）——复活守卫与质询闭环都要读全状态。"""
    conditions = (ProfileBelief.user_id == user_id, ProfileBelief.key == key)
    return session.scalar(select(ProfileBelief).where(*conditions))


def get_active_belief(session: Session, user_id: str, key: str) -> ProfileBelief | None:
    belief = get_belief(session, user_id, key)
    if belief is not None and belief.status == "active":
        return belief
    return None


def list_active_beliefs(
    session: Session,
    user_id: str,
    *,
    dimensions: tuple[str, ...] | None = None,
    limit: int = 50,
) -> list[ProfileBelief]:
    """当前生效信念，按证据时近排序（渲染排序是记忆模块的职责）。"""
    if not is_profile_memory_enabled(session, user_id):
        return []
    conditions = [ProfileBelief.user_id == user_id, ProfileBelief.status == "active"]
    if dimensions:
        conditions.append(ProfileBelief.dimension.in_(dimensions))
    stmt = select(ProfileBelief).where(*conditions).order_by(desc(ProfileBelief.last_evidence_at)).limit(limit)
    return list(session.scalars(stmt))


def get_belief_events(session: Session, user_id: str, belief_id: int, *, limit: int = 50) -> list[ProfileBeliefEvent]:
    conditions = (ProfileBeliefEvent.user_id == user_id, ProfileBeliefEvent.belief_id == belief_id)
    stmt = select(ProfileBeliefEvent).where(*conditions).order_by(desc(ProfileBeliefEvent.id)).limit(limit)
    return list(session.scalars(stmt))


def record_claim(
    session: Session,
    user_id: str,
    *,
    dimension: str,
    key: str,
    claim_text: str,
    value: dict | None = None,
    relation: str = "supports",
    confidence: float = 0.0,
    layer: str = "L4",
    source: str = "extracted",
    session_id: str | None = None,
    evidence_message_ids: list[int] | None = None,
    stats_id: int | None = None,
    origin_slice_id: str | None = None,
    context_tags: list[str] | None = None,
) -> tuple[ProfileBelief | None, str]:
    """写入一条 claim，按合并策略落到 created / supported / contradicted /
    downgraded；命中否决守卫返回 (None, "resurrection_guard")。

    返回的事件名同时是 profile_belief_events.event_type。
    """
    if not is_profile_memory_enabled(session, user_id):
        return None, "profile_memory_disabled"
    if dimension not in _VALID_DIMENSIONS:
        raise ValueError(f"invalid profile dimension: {dimension!r}")
    if key.startswith("distortion."):
        # 红线 §7.2：认知歪曲只许当轮识别，结构上禁止沉淀。
        raise ValueError(f"distortion keys are identify-only and cannot be persisted: {key!r}")
    if relation not in {"supports", "contradicts"}:
        raise ValueError(f"invalid claim relation: {relation!r}")

    evidence = [int(mid) for mid in (evidence_message_ids or [])]

    # 提取器产出一律 L4：L2 只能来自用户确认（confirm_belief）或程序硬证据。
    if source == "extracted" and layer != "L4":
        logger.warning("Clamping non-L4 layer from extracted claim (layer=%s)", layer)
        layer = "L4"

    existing = get_belief(session, user_id, key)
    if existing is not None and existing.status == "rejected":
        # D8 负记忆：否决过的 key 永不复活（也不记事件——守卫本身零痕迹，
        # 避免事件表被反复试探刷行）。
        return None, "resurrection_guard"

    if existing is None:
        # 跨情境标签：合并到 value_json，支持跨情境证据聚合。
        merged_value = dict(value or {})
        if context_tags:
            existing_tags = merged_value.get("context_tags", [])
            merged_value["context_tags"] = sorted(set(existing_tags) | set(context_tags))
        belief = ProfileBelief(
            user_id=user_id,
            dimension=dimension,
            key=key,
            claim_text=claim_text,
            value_json=json.dumps(merged_value, ensure_ascii=False),
            layer=layer,
            status="active",
            confidence=min(CONFIDENCE_CEILING, max(0.0, confidence)),
            source=source,
            evidence_json=json.dumps(evidence[-MAX_EVIDENCE_REFS:]),
            origin_stats_id=stats_id,
            origin_slice_id=origin_slice_id,
            origin_session_id=session_id,
            last_evidence_session_id=session_id,
        )
        session.add(belief)
        session.flush()
        _append_event(session, belief, "created", evidence=evidence, stats_id=stats_id)
        return belief, "created"

    if relation == "supports":
        # 8 维置信度加权：合并新证据后重新计算。
        merged_evidence = json.loads(existing.evidence_json or "[]")
        for mid in evidence:
            if mid not in merged_evidence:
                merged_evidence.append(mid)
        existing.evidence_json = json.dumps(merged_evidence[-MAX_EVIDENCE_REFS:])
        existing.last_evidence_session_id = session_id
        existing.last_evidence_at = datetime.now(UTC)

        # 跨情境标签：支持证据的情境合并到 value_json。
        if context_tags:
            try:
                val = json.loads(existing.value_json or "{}")
            except (TypeError, ValueError):
                val = {}
            old_tags = val.get("context_tags", [])
            val["context_tags"] = sorted(set(old_tags) | set(context_tags))
            existing.value_json = json.dumps(val, ensure_ascii=False)

        # 多因子置信度：基础增益 × 8 维权重。
        now = datetime.now(UTC)
        last_ev = existing.last_evidence_at
        if last_ev.tzinfo is None:
            last_ev = last_ev.replace(tzinfo=UTC)
        days_since = max(0.0, (now - last_ev).total_seconds() / 86400)
        try:
            val = json.loads(existing.value_json or "{}")
            ctx_div = len(val.get("context_tags", []))
        except (TypeError, ValueError):
            ctx_div = 0
        events = get_belief_events(session, existing.user_id, existing.id)
        contradict_count = sum(1 for e in events if e.event_type in {"contradicted", "downgraded"})
        support_count = len(merged_evidence)

        weight = compute_confidence_weight(
            existing,
            support_count=support_count,
            contradict_count=contradict_count,
            context_diversity=ctx_div,
            days_since_last_evidence=days_since,
        )
        existing.confidence = min(CONFIDENCE_CEILING, existing.confidence + SUPPORT_GAIN * weight)
        _append_event(
            session,
            existing,
            "supported",
            evidence=evidence,
            detail={"confidence": existing.confidence},
            stats_id=stats_id,
        )
        return existing, "supported"

    # contradicts：矛盾证据标记 needs_clarification，等待验证半环路澄清。
    # 不一致处理升级（2026-09-15）：保留矛盾双方，标记待澄清，不直接降级。
    # 置信度衰减从 0.5 放宽到 0.8（减少对旧信念的惩罚）；L2 不再自动降级。
    existing.confidence = round(existing.confidence * 0.8, 4)
    event_type = "contradicted"
    # 标记 needs_clarification 到 value_json。
    try:
        val = json.loads(existing.value_json or "{}")
    except (TypeError, ValueError):
        val = {}
    val["clarification_status"] = "needs_clarification"
    existing.value_json = json.dumps(val, ensure_ascii=False)
    detail: dict = {"confidence": existing.confidence, "clarification_status": "needs_clarification"}
    _append_event(session, existing, event_type, evidence=evidence, detail=detail, stats_id=stats_id)
    return existing, event_type


def list_rejected_beliefs(session: Session, user_id: str, *, limit: int = 5) -> list[ProfileBelief]:
    """D8 负记忆：被否决信念，按时近取最近 N 条（渲染为应回避清单）。"""
    if not is_profile_memory_enabled(session, user_id):
        return []
    conditions = (ProfileBelief.user_id == user_id, ProfileBelief.status == "rejected")
    stmt = select(ProfileBelief).where(*conditions).order_by(desc(ProfileBelief.updated_at)).limit(limit)
    return list(session.scalars(stmt))


# 质询候选的干预价值分级（调参 A，2026-09-13 线上实测驱动）：D1 主题的
# LLM 置信度天然高（0.95+），纯置信度排序会让提问预算永远花在主题上——
# 而主题在面板已经可见，质询的稀缺预算应该验证真正改变干预策略的
# 机制/动机信念：跨过水位的候选先按分级、再按置信度排序。
_QUESTION_VALUE_TIER = {"D3": 3, "D5": 2}
# 确认率反馈的权重闭区间：静态分级 × 学习权重。
# [0.6, 1.4] 的设计意图——只在同优先级带内调整，不推翻"机制类(D3)整体
# 优先于主题类(D1)"的 ADR：D3 最差 3×0.6=1.8 仍高于 D1 最好 1×1.4=1.4；
# 但确认率低的 D3（1.8）会被确认率高的 D5（2×1.4=2.8）反超，把提问
# 预算让给该用户身上"问得准"的维度。
QUESTION_TIER_WEIGHT_MIN = 0.6
QUESTION_TIER_WEIGHT_MAX = 1.4


def _question_confirm_rate_weight(rate: float) -> float:
    """确认率 [0,1] → 权重 [MIN, MAX] 的线性映射；rate=0.5 时恰为 1.0。"""
    clamped = min(max(rate, 0.0), 1.0)
    weight = QUESTION_TIER_WEIGHT_MIN + (QUESTION_TIER_WEIGHT_MAX - QUESTION_TIER_WEIGHT_MIN) * clamped
    return round(weight, 4)


def _dimension_question_weights(session: Session, user_id: str) -> dict[str, float]:
    """各维度的质询价值学习权重（按该用户历史质询结局计算）。

    数据源：``question_answered`` 事件 detail={verdict, belief_key}——
    confirm 计成功，deny/unclear 均计失败（unclear 同样花掉了一次提问
    预算）。belief_key → dimension 经 profile_beliefs 表现存关联解析，
    解析不出（极端边界）的事件跳过。

    小样本用 Beta(1,1) 收缩：``rate = (confirms+1)/(n+2)``，n=0 → 0.5
    → 权重 1.0（静态分级原样生效），n 越大反馈越强。
    """
    answered = list(
        session.scalars(
            select(ProfileInterventionEvent).where(
                ProfileInterventionEvent.user_id == user_id,
                ProfileInterventionEvent.intervention_kind == "question_answered",
            )
        )
    )
    if not answered:
        return {}

    key_to_dim = dict(
        session.execute(
            select(ProfileBelief.key, ProfileBelief.dimension).where(ProfileBelief.user_id == user_id)
        ).all()
    )
    stats: dict[str, list[int]] = {}
    for event in answered:
        try:
            detail = json.loads(event.detail_json or "{}")
        except (TypeError, ValueError):
            continue
        dimension = key_to_dim.get(str(detail.get("belief_key") or ""))
        if not dimension:
            continue
        slot = stats.setdefault(dimension, [0, 0])
        slot[1] += 1
        if detail.get("verdict") == "confirm":
            slot[0] += 1
    return {
        dimension: _question_confirm_rate_weight((confirms + 1) / (total + 2))
        for dimension, (confirms, total) in stats.items()
    }


def _needs_clarification_boost(belief: ProfileBelief) -> int:
    """不一致处理：needs_clarification 的信念优先排入质询候选。"""
    try:
        val = json.loads(belief.value_json or "{}")
    except (TypeError, ValueError):
        return 0
    return 1 if val.get("clarification_status") == "needs_clarification" else 0


def list_question_candidates(session: Session, user_id: str, *, limit: int = 1) -> list[ProfileBelief]:
    """质询候选：L4 且 confidence ≥ 水位的待验证假设（K2 质询闭环数据源）。

    只在确定性水位之上才值得花一次提问预算；升级 L2 仍需用户确认。
    排序 = （干预价值分级 × 该维度确认率学习权重，置信度，时近）降序。
    无质询历史时权重全为 1.0，回退为静态分级（D3>D5>其余）。
    """
    if not is_profile_memory_enabled(session, user_id):
        return []
    conditions = (
        ProfileBelief.user_id == user_id,
        ProfileBelief.status == "active",
        ProfileBelief.layer == "L4",
    )
    rows = list(session.scalars(select(ProfileBelief).where(*conditions)))
    # 质询候选门槛：置信度 >= 阈值 且 跨会话证据（origin != last_evidence）。
    pending = [
        b
        for b in rows
        if b.confidence >= L4_QUESTION_THRESHOLD
        and (
            not b.origin_session_id
            or not b.last_evidence_session_id
            or b.origin_session_id != b.last_evidence_session_id
        )
    ]
    weights = _dimension_question_weights(session, user_id)
    pending.sort(
        key=lambda b: (
            _needs_clarification_boost(b),
            _QUESTION_VALUE_TIER.get(b.dimension, 1) * weights.get(b.dimension, 1.0),
            b.confidence,
            b.last_evidence_at,
        ),
        reverse=True,
    )
    return pending[:limit]


def record_intervention_event(
    session: Session,
    user_id: str,
    *,
    session_id: str,
    kind: str,
    detail: dict | None = None,
) -> ProfileInterventionEvent | None:
    """干预→反应事件（K2c）：只记动作元数据，不记对话内容。"""
    if not is_profile_memory_enabled(session, user_id):
        return None
    row = ProfileInterventionEvent(
        user_id=user_id,
        session_id=session_id,
        intervention_kind=kind,
        detail_json=json.dumps(detail or {}, ensure_ascii=False),
    )
    session.add(row)
    session.flush()
    return row


def record_protective_belief(
    session: Session,
    user_id: str,
    key: str,
    *,
    value: dict | None = None,
    claim_text: str = "",
    consented: bool,
    session_id: str | None = None,
) -> ProfileBelief | None:
    """D7 保护因子写入（K3）：知情同意是结构性门控，未经同意直接拒绝。

    仅接受 protective.* 命名空间且在展示词典中登记的 key；用户在安全
    计划流程中的亲口提供 = user_stated / L1。未同意时返回 None 且
    不留任何行（同 distortion 红线：结构上不给入口）。
    """
    if not consented:
        return None
    if not key.startswith("protective."):
        raise ValueError(f"protective beliefs must use the protective. namespace: {key!r}")
    return record_claim(
        session,
        user_id,
        dimension="D7",
        key=key,
        claim_text=claim_text or f"用户在知情同意下提供了保护因子：{key}",
        value=value,
        confidence=0.9,
        layer="L1",
        source="user_stated",
        session_id=session_id,
    )[0]


def get_pending_verification(
    session: Session,
    user_id: str,
    session_id: str,
) -> ProfileInterventionEvent | None:
    """回半环数据源：未回应的质询注入，且用户已至少回复一次（窗口 ≤3 轮）。

    同会话约束不变；1-3 轮内允许迟到判定（用户可能绕一下再回来表态），
    超过 3 轮视为话题已走，作废不再追溯（质询的时机性是体验的一部分）。
    """
    from psych_support_bot.infra.db.models import Message

    conditions = (
        ProfileInterventionEvent.user_id == user_id,
        ProfileInterventionEvent.session_id == session_id,
        ProfileInterventionEvent.intervention_kind == "question_injected",
    )
    injection = session.scalar(
        select(ProfileInterventionEvent).where(*conditions).order_by(desc(ProfileInterventionEvent.id)).limit(1)
    )
    if injection is None:
        return None
    later_user_msgs = (
        session.query(Message.id)
        .filter(
            Message.session_id == session_id,
            Message.role == "user",
            Message.created_at > injection.created_at,
        )
        .count()
    )
    return injection if 1 <= later_user_msgs <= 3 else None


def has_unanswered_injection(session: Session, user_id: str) -> bool:
    """是否存在尚未被判定的质询注入（回半环去重依据）。

    同一时间只允许一个悬而未决的假设：上一条 question_injected 之后
    还没有 question_answered 时，不再注入新质询——否则当前轮的新注入
    会遮蔽上一轮的注入，回半环（"注入后恰好一条用户消息"）永远无法命中。
    """
    conditions = (
        ProfileInterventionEvent.user_id == user_id,
        ProfileInterventionEvent.intervention_kind == "question_injected",
    )
    last_injected = session.scalar(
        select(ProfileInterventionEvent).where(*conditions).order_by(desc(ProfileInterventionEvent.id)).limit(1)
    )
    if last_injected is None:
        return False
    answer_conditions = (
        ProfileInterventionEvent.user_id == user_id,
        ProfileInterventionEvent.intervention_kind == "question_answered",
    )
    last_answered = session.scalar(
        select(ProfileInterventionEvent).where(*answer_conditions).order_by(desc(ProfileInterventionEvent.id)).limit(1)
    )
    return last_answered is None or last_answered.id < last_injected.id


def update_belief_value(
    session: Session,
    user_id: str,
    key: str,
    value: dict,
    *,
    stats_id: int | None = None,
    evidence: list[int] | None = None,
) -> ProfileBelief | None:
    """更新信念的结构化取值（value_json），原值留痕于事件表。

    support 合并不覆盖 value（D4 的 neutral→worked 转变等学习信号
    靠本原语显式更新，旧值可从事件流追溯）。
    """
    belief = get_belief(session, user_id, key)
    if belief is None or belief.status != "active":
        return None
    previous = belief.value_json
    belief.value_json = json.dumps(value, ensure_ascii=False)
    _append_event(
        session,
        belief,
        "value_updated",
        evidence=evidence or [],
        detail={"previous_value": previous, "value": value},
        stats_id=stats_id,
    )
    return belief


def confirm_belief(session: Session, user_id: str, belief_id: int) -> ProfileBelief | None:
    """用户确认：L4 → L2（质询闭环的升级原语，只认本人行）。"""
    belief = session.get(ProfileBelief, belief_id)
    if belief is None or belief.user_id != user_id or belief.status != "active":
        return None
    belief.layer = "L2"
    belief.source = "user_confirmed"
    belief.confidence = max(belief.confidence, 0.9)
    _append_event(session, belief, "confirmed", evidence=[], detail={"layer": "L2"})
    return belief


def reject_belief(session: Session, user_id: str, belief_id: int) -> ProfileBelief | None:
    """用户否决：status → rejected（D8 负记忆，同 key 永不复活、永不质询）。"""
    belief = session.get(ProfileBelief, belief_id)
    if belief is None or belief.user_id != user_id or belief.status != "active":
        return None
    belief.status = "rejected"
    belief.confidence = 0.0
    _append_event(session, belief, "rejected", evidence=[], detail={"reason": "user_denied"})
    return belief


def delete_user_profile_beliefs(session: Session, user_id: str) -> dict[str, int]:
    """级联删除某用户全部推断画像和行为时间画像数据。"""
    events_deleted = (
        session.query(ProfileBeliefEvent)
        .filter(ProfileBeliefEvent.user_id == user_id)
        .delete(synchronize_session=False)
    )
    beliefs_deleted = (
        session.query(ProfileBelief).filter(ProfileBelief.user_id == user_id).delete(synchronize_session=False)
    )
    stats_deleted = (
        session.query(ProfileExtractionStats)
        .filter(ProfileExtractionStats.user_id == user_id)
        .delete(synchronize_session=False)
    )
    interventions_deleted = (
        session.query(ProfileInterventionEvent)
        .filter(ProfileInterventionEvent.user_id == user_id)
        .delete(synchronize_session=False)
    )
    time_profile_deleted = (
        session.query(UserTimeProfile).filter(UserTimeProfile.user_id == user_id).delete(synchronize_session=False)
    )
    # 清除 UserProfile 上的画像扩展字段。
    from psych_support_bot.infra.db.models import ProfileEvolutionJob, ProfileSnapshot, UserProfile

    profile = session.get(UserProfile, user_id)
    bg_cleared = 0
    if profile and (profile.background_json or profile.understanding_json):
        profile.background_json = "{}"
        profile.understanding_json = "{}"
        bg_cleared = 1
    # 清除异步任务和快照。
    jobs_deleted = (
        session.query(ProfileEvolutionJob)
        .filter(ProfileEvolutionJob.user_id == user_id)
        .delete(synchronize_session=False)
    )
    snapshots_deleted = (
        session.query(ProfileSnapshot).filter(ProfileSnapshot.user_id == user_id).delete(synchronize_session=False)
    )
    return {
        "profile_beliefs": int(beliefs_deleted),
        "profile_belief_events": int(events_deleted),
        "profile_extraction_stats": int(stats_deleted),
        "profile_intervention_events": int(interventions_deleted),
        "user_time_profiles": int(time_profile_deleted),
        "profile_background_cleared": bg_cleared,
        "profile_evolution_jobs": int(jobs_deleted),
        "profile_snapshots": int(snapshots_deleted),
    }
