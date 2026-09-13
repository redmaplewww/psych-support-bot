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

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from psych_support_bot.infra.db.models import (
    ProfileBelief,
    ProfileBeliefEvent,
    ProfileExtractionStats,
    ProfileInterventionEvent,
)

logger = logging.getLogger(__name__)

# belief 行上只保留最近 N 条证据 message id；完整史在事件表。
MAX_EVIDENCE_REFS = 8

# 置信度更新的确定性参数（K2 接入质询闭环后按 eval 数据再校准）。
SUPPORT_GAIN = 0.15
CONTRADICT_FACTOR = 0.5
CONFIDENCE_CEILING = 0.95

# 质询候选水位：≥此值且跨会话证据 ≥2 才允许被质询（升级 L2 仍需用户确认）。
L4_QUESTION_THRESHOLD = 0.7

_VALID_DIMENSIONS = {f"D{i}" for i in range(1, 9)}


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
) -> tuple[ProfileBelief | None, str]:
    """写入一条 claim，按合并策略落到 created / supported / contradicted /
    downgraded；命中否决守卫返回 (None, "resurrection_guard")。

    返回的事件名同时是 profile_belief_events.event_type。
    """
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
        logger.warning("Clamping non-L4 layer from extracted claim, key=%s layer=%s", key, layer)
        layer = "L4"

    existing = get_belief(session, user_id, key)
    if existing is not None and existing.status == "rejected":
        # D8 负记忆：否决过的 key 永不复活（也不记事件——守卫本身零痕迹，
        # 避免事件表被反复试探刷行）。
        return None, "resurrection_guard"

    if existing is None:
        belief = ProfileBelief(
            user_id=user_id,
            dimension=dimension,
            key=key,
            claim_text=claim_text,
            value_json=json.dumps(value or {}, ensure_ascii=False),
            layer=layer,
            status="active",
            confidence=min(CONFIDENCE_CEILING, max(0.0, confidence)),
            source=source,
            evidence_json=json.dumps(evidence[-MAX_EVIDENCE_REFS:]),
            origin_stats_id=stats_id,
            origin_session_id=session_id,
            last_evidence_session_id=session_id,
        )
        session.add(belief)
        session.flush()
        _append_event(session, belief, "created", evidence=evidence, stats_id=stats_id)
        return belief, "created"

    if relation == "supports":
        existing.confidence = min(CONFIDENCE_CEILING, existing.confidence + SUPPORT_GAIN)
        merged = json.loads(existing.evidence_json or "[]")
        for mid in evidence:
            if mid not in merged:
                merged.append(mid)
        existing.evidence_json = json.dumps(merged[-MAX_EVIDENCE_REFS:])
        existing.last_evidence_session_id = session_id
        _append_event(
            session,
            existing,
            "supported",
            evidence=evidence,
            detail={"confidence": existing.confidence},
            stats_id=stats_id,
        )
        return existing, "supported"

    # contradicts：矛盾证据衰减；L2 降级回 L4 等待验证（不覆盖不删除）。
    existing.confidence = round(existing.confidence * CONTRADICT_FACTOR, 4)
    event_type = "contradicted"
    detail: dict = {"confidence": existing.confidence}
    if existing.layer == "L2":
        existing.layer = "L4"
        event_type = "downgraded"
        detail["previous_layer"] = "L2"
    _append_event(session, existing, event_type, evidence=evidence, detail=detail, stats_id=stats_id)
    return existing, event_type


def list_rejected_beliefs(session: Session, user_id: str, *, limit: int = 5) -> list[ProfileBelief]:
    """D8 负记忆：被否决信念，按时近取最近 N 条（渲染为应回避清单）。"""
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
        session.execute(select(ProfileBelief.key, ProfileBelief.dimension).where(ProfileBelief.user_id == user_id))
        .all()
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


def list_question_candidates(session: Session, user_id: str, *, limit: int = 1) -> list[ProfileBelief]:
    """质询候选：L4 且 confidence ≥ 水位的待验证假设（K2 质询闭环数据源）。

    只在确定性水位之上才值得花一次提问预算；升级 L2 仍需用户确认。
    排序 = （干预价值分级 × 该维度确认率学习权重，置信度，时近）降序。
    无质询历史时权重全为 1.0，回退为静态分级（D3>D5>其余）。
    """
    conditions = (
        ProfileBelief.user_id == user_id,
        ProfileBelief.status == "active",
        ProfileBelief.layer == "L4",
    )
    rows = list(session.scalars(select(ProfileBelief).where(*conditions)))
    pending = [b for b in rows if b.confidence >= L4_QUESTION_THRESHOLD]
    weights = _dimension_question_weights(session, user_id)
    pending.sort(
        key=lambda b: (
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
) -> ProfileInterventionEvent:
    """干预→反应事件（K2c）：只记动作元数据，不记对话内容。"""
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
    """级联删除某用户全部画像数据（接入 /v1/me 删除链）。"""
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
    return {
        "profile_beliefs": int(beliefs_deleted),
        "profile_belief_events": int(events_deleted),
        "profile_extraction_stats": int(stats_deleted),
        "profile_intervention_events": int(interventions_deleted),
    }
