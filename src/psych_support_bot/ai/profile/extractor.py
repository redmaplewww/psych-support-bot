"""画像轮次提取器（K1b 确定性信号 + K2 LLM 语义提取）。

设计约束（docs/plans/profile-memory-knowledge.md §5/§6、PROFILE_DECISIONS P2）：
- 挂载点：services/conversation.py `_finalize` 末尾（消息落库之后，
  证据 message id 才存在）；fail-open——提取失败只记统计，绝不阻断对话。
- D1 复用图内现成信号：`state["topics"]` 是 LLM 语义主题（闭集校验）与
  关键词主题的并集，本轮零额外 LLM 成本。
- D4 吃练习完成信号：自报完成（detect_completed_exercise）与引导完成
  （practice_action="complete"），效果词从同轮用户话术做确定性扫描
  （负向词先判——"没什么用"包含"有用"子串）；无效果词记 neutral 基线。
- 危机轮（high/critical）零提取：高危内容不入画像层，只走 RiskEvent
  通道（知识提炼 §3 硬规则的提取侧实现）。
- P2 全量统计：每个收尾轮一行 profile_extraction_stats（含危机守卫与
  禁用跳过不记——禁用是开关不是调用）；K1 无 LLM 成本，节流与熔断阀
  留待 K2 引入 LLM 提取时启用。
- 单次 ≤3 条 claim；效果值经 update_belief_value 更新（neutral→worked
  的转变是 D4 的核心学习信号），belief key 恒为练习 tag。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from psych_support_bot.ai.profile.semantic import run_semantic_extraction, run_verification_judgment
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.models import Message
from psych_support_bot.infra.db.profile_repositories import (
    record_claim,
    record_extraction_stats,
    update_belief_value,
)

logger = logging.getLogger(__name__)

# 单次提取的 claim 总量上限（知识提炼 §5 契约）。
MAX_CLAIMS_PER_TURN = 3

# 危机轮不提取（与 fixture neg_crisis 金标准一致）。
_CRISIS_LEVELS = {"high", "critical"}

# 效果词确定性扫描（K2 交 LLM 前的保守词表）。负向先判：
# "没什么用"含"有用"子串，顺序错会把负反馈记成正向。
_VALENCE_AVERSIVE: tuple[str, ...] = (
    "没什么用",
    "没有用",
    "没啥用",
    "不管用",
    "帮不上",
    "更糟",
    "更乱",
    "更焦虑",
    "更烦躁",
    "更难受",
    "不舒服",
    "不适合我",
    "做不下去",
    "坚持不下来",
    "worse",
    "didn't help",
    "not helpful",
    "not for me",
)
_VALENCE_WORKED: tuple[str, ...] = (
    "很有帮助",
    "有帮助",
    "真的有用",
    "挺有用",
    "有用",
    "帮到我",
    "帮到我了",
    "舒服多了",
    "轻松多了",
    "平静多了",
    "好多了",
    "helped",
    "helpful",
    "worked for me",
    "feeling calmer",
)


@dataclass(frozen=True)
class ProfileClaim:
    """单条提取产出——record_claim 的入参载体（契约形状见知识提炼 §5）。"""

    dimension: str
    key: str
    claim_text: str
    value: dict = field(default_factory=dict)
    relation: str = "supports"
    confidence: float = 0.0
    evidence_message_ids: list[int] = field(default_factory=list)


def scan_valence(text: str) -> str:
    """确定性效果判定：aversive / worked / neutral。负向词优先。"""
    lowered = (text or "").lower()
    if any(marker in lowered for marker in _VALENCE_AVERSIVE):
        return "aversive"
    if any(marker in lowered for marker in _VALENCE_WORKED):
        return "worked"
    return "neutral"


def d1_topic_claims(topics: list[str], *, evidence_message_id: int | None) -> list[ProfileClaim]:
    """D1 关注主题：图内 topics 逐条成 claim（调用方负责总量截断）。"""
    evidence = [evidence_message_id] if evidence_message_id else []
    return [
        ProfileClaim(
            dimension="D1",
            key=topic,
            claim_text=f"对话中出现主题信号：{topic}",
            value={"via": "graph_topics"},
            confidence=0.4,
            evidence_message_ids=evidence,
        )
        for topic in topics
    ]


def d4_intervention_claim(
    exercise_tag: str,
    *,
    valence_text: str,
    evidence_message_id: int | None,
) -> ProfileClaim:
    """D4 干预响应：练习完成 + 同轮效果词。belief key 恒为练习 tag。

    调参 C（2026-09-13）：明确效价（worked/aversive）单次即达面板水位
    0.55——用户亲口说"有用/没用"是直接反馈，不该和模糊信号一样攒两次；
    neutral 保持 0.3 基线（完成事实 ≠ 效果判断）。
    """
    effect = scan_valence(valence_text)
    confidence = {"worked": 0.55, "aversive": 0.55, "neutral": 0.3}[effect]
    claim_text = {
        "worked": f"练习 {exercise_tag} 完成后用户表达了正向反馈",
        "aversive": f"练习 {exercise_tag} 使用中用户表达了负向反馈",
        "neutral": f"用户完成了练习 {exercise_tag}（本轮无明确效果反馈）",
    }[effect]
    evidence = [evidence_message_id] if evidence_message_id else []
    return ProfileClaim(
        dimension="D4",
        key=exercise_tag,
        claim_text=claim_text,
        value={"tag": exercise_tag, "effect": effect},
        confidence=confidence,
        evidence_message_ids=evidence,
    )


def _latest_user_message_id(session: Session, session_id: str) -> int | None:
    stmt = (
        select(Message.id)
        .where(Message.session_id == session_id, Message.role == "user")
        .order_by(desc(Message.id))
        .limit(1)
    )
    return session.scalar(stmt)


# 量表/测评语境标记：用户在这些语境里提到的情绪词是"测评行为"的组成部分
# （如"焦虑量表没测准"），不是当期话题陈述——行为层频率信号不进画像内容层
# （fixture redline_reassurance_retest 金标准）。命中即抑制本轮 D1 主题
# claim（保守豁免：漏一轮的主题会在后续无测评语境的轮次自然累积）。
_ASSESSMENT_CONTEXT_MARKERS: tuple[str, ...] = (
    "量表",
    "测评",
    "评分",
    "重测",
    "测一次",
    "测一下",
    "phq",
    "gad-7",
    "gad7",
    "questionnaire",
    "retake the",
    "retest",
)


def is_assessment_context(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _ASSESSMENT_CONTEXT_MARKERS)


def build_turn_claims(
    *,
    topics: list[str],
    risk_level: str,
    exercise_tag: str | None,
    valence_text: str,
) -> list[ProfileClaim]:
    """组装本轮 claims：D4 优先保留（更稀缺），D1 填充至总量上限。"""
    if risk_level in _CRISIS_LEVELS:
        return []
    claims: list[ProfileClaim] = []
    if exercise_tag:
        claims.append(d4_intervention_claim(exercise_tag, valence_text=valence_text, evidence_message_id=None))
    # D1 抑制（两条确定性规则，均为 fixture 金标准）：
    # - 量表语境：测评行为语境中的情绪词不是当期话题（行为层信号不进内容层）；
    # - 练习完成轮：完成叙述里的情绪词是练习使用的情境，不是当期话题陈述。
    # 保守豁免的漏检会在后续无此语境的轮次自然累积，代价可接受。
    if exercise_tag or is_assessment_context(valence_text):
        d1_topics: list[str] = []
    else:
        d1_topics = topics
    remaining = MAX_CLAIMS_PER_TURN - len(claims)
    claims.extend(d1_topic_claims(d1_topics[:remaining], evidence_message_id=None))
    return claims


def run_turn_extraction(
    session: Session,
    *,
    user_id: str,
    session_id: str,
    topics: list[str],
    risk_level: str,
    exercise_tag: str | None,
    valence_text: str,
    turn_count: int = 0,
) -> None:
    """每轮收尾提取入口（_finalize 挂载点）。fail-open，绝不抛出。

    顺序：确定性提取（K1，零成本）→ LLM 语义提取（K2，节流触发、
    危机轮跳过）。两层各自独立提交、独立统计，互不阻断。
    """
    if not get_settings().profile_extraction_enabled:
        return
    crisis = risk_level in _CRISIS_LEVELS
    trigger = "crisis_guard" if crisis else ("practice_event" if exercise_tag else "topic_flow")
    try:
        stats = record_extraction_stats(
            session,
            user_id,
            session_id=session_id,
            trigger=trigger,
            model="deterministic/k1",
        )
        claims = build_turn_claims(
            topics=topics,
            risk_level=risk_level,
            exercise_tag=exercise_tag,
            valence_text=valence_text,
        )
        message_id = _latest_user_message_id(session, session_id)
        for claim in claims:
            belief, _event = record_claim(
                session,
                user_id,
                dimension=claim.dimension,
                key=claim.key,
                claim_text=claim.claim_text,
                value=claim.value,
                relation=claim.relation,
                confidence=claim.confidence,
                session_id=session_id,
                evidence_message_ids=claim.evidence_message_ids or ([message_id] if message_id else []),
                stats_id=stats.id,
            )
            # 效果值更新：neutral→worked/aversive 的转变是 D4 的学习信号；
            # belief 行的 value_json 不随 support 自动覆盖，需显式更新。
            if belief is not None and claim.dimension == "D4" and claim.value.get("effect"):
                update_belief_value(
                    session,
                    user_id,
                    claim.key,
                    claim.value,
                    stats_id=stats.id,
                    evidence=[message_id] if message_id else [],
                )
        stats.claims_out = len(claims)
        session.commit()
    except Exception:
        logger.warning("Profile extraction failed for user %s; skipping turn.", user_id, exc_info=True)
        session.rollback()
        try:
            # 失败也要留 P2 统计痕迹（error 行），但统计写入自身失败则放弃。
            record_extraction_stats(
                session,
                user_id,
                session_id=session_id,
                trigger=trigger,
                model="deterministic/k1",
                status="error",
                error="extraction_failed",
            )
            session.commit()
        except Exception:  # noqa: BLE001 — fail-open 的兜底自身也必须兜住
            session.rollback()

    # 回半环（K2 收尾）：上一轮若质询过且本轮是首次回应，先判定应答并
    # 驱动 confirm/reject；质询判定占用本轮时跳过常规语义提取（该轮的
    # 信号属于"对假设的回应"，不是新主题）。
    verification_handled = run_verification_judgment(
        session,
        user_id=user_id,
        session_id=session_id,
        user_text=valence_text,
    )

    # K2 LLM 语义提取（独立提交与统计；内部自带节流/危机门控/fail-open）。
    if not verification_handled:
        run_semantic_extraction(
            session,
            user_id=user_id,
            session_id=session_id,
            user_text=valence_text,
            turn_count=turn_count,
            risk_level=risk_level,
            practice_event=bool(exercise_tag),
        )


def record_turn_interventions(
    session: Session,
    *,
    user_id: str,
    session_id: str,
    practice_action: str,
    exercise_tag: str | None,
    question_candidates: list[str],
    no_question_mode: bool,
    mode: str,
    risk_level: str,
) -> None:
    """K2c 干预→反应事件：只记动作元数据，结果由事件序列派生。fail-open。"""
    from psych_support_bot.infra.db.profile_repositories import (
        has_unanswered_injection,
        record_intervention_event,
    )

    if practice_action in {"offer", "start", "complete"}:
        record_intervention_event(
            session,
            user_id,
            session_id=session_id,
            kind=f"practice_{practice_action}",
            detail={"tag": exercise_tag or "panic_grounding_5_4_3_2_1"},
        )
    if question_candidates and not no_question_mode and mode != "crisis" and risk_level not in _CRISIS_LEVELS:
        # 上一条注入尚未被判定时不重复注入：否则当前轮的新注入（晚于本轮
        # 用户消息）会遮蔽上一轮，回半环（"注入后恰好一条用户消息"）永远
        # 无法命中——线上 V1/V2 实测定位到的就是这个问题。
        if has_unanswered_injection(session, user_id):
            logger.info("Question injection skipped: previous injection still unanswered.")
        else:
            # 标签是系统生成的友善措辞，不是对话内容（UsageEvent 伦理边界同源）。
            record_intervention_event(
                session,
                user_id,
                session_id=session_id,
                kind="question_injected",
                detail={"belief_label": question_candidates[0]},
            )
    session.commit()
