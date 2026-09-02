import contextvars
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from psych_support_bot.ai.safety.llm_classifier import classify_risk_llm
from psych_support_bot.ai.safety.rules import classify_message_risk
from psych_support_bot.ai.schemas.messages import RiskResult
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output

logger = logging.getLogger(__name__)

_SEVERITY = {"low": 0, "elevated": 1, "high": 2, "critical": 3}


def _merge_upgrade(rule: RiskResult, llm: RiskResult) -> RiskResult:
    """单向升级阀门：取 max(规则, LLM)，LLM 永远不能把规则判定拉回。"""
    if _SEVERITY[llm.risk_level] <= _SEVERITY[rule.risk_level]:
        return rule
    return RiskResult(
        risk_level=llm.risk_level,
        risk_types=[*dict.fromkeys([*rule.risk_types, *llm.risk_types])],
        needs_crisis_mode=rule.needs_crisis_mode or llm.needs_crisis_mode,
        reason=f"{llm.reason} (rule verdict kept in types: {rule.reason})",
    )


# Patterns to detect previous elevated risk in memory_summary.
# Memory snapshot contains recent messages and risk info; we look for
# signs that the previous turn was classified as elevated distress.
_PREV_ELEVATED_PATTERNS = [
    # Risk level markers in session summary format
    r"risk\s*=\s*elevated",
    r"risk_level.*elevated",
    # Elevated distress keywords appearing in recent message excerpts
    # (these overlap with ELEVATED_RISK_KEYWORDS from rules.py)
    r"绝望|没有希望|没意义|撑不住|扛不住|快崩溃|睡不着|失眠|惊恐发作|喘不过气",
    r"hopeless|panic attack|worthless|not sleeping|better off dead",
]

_PREV_ELEVATED_REGEX = [re.compile(p, re.IGNORECASE) for p in _PREV_ELEVATED_PATTERNS]


def _has_previous_elevated(memory_summary: str) -> bool:
    """Check if memory_summary indicates the previous turn was elevated risk."""
    if not memory_summary:
        return False
    return any(pattern.search(memory_summary) for pattern in _PREV_ELEVATED_REGEX)


def _speculation_enabled() -> bool:
    from psych_support_bot.infra.config.settings import get_settings

    return get_settings().speculative_reply_enabled


def _prepare_speculative_args(state: GraphState) -> dict | None:
    """投机回复入参准备（仅 support 模式——最常见路径）。

    所有参数来自确定性输入：detect_mode 关键词检测、determine_interview_process
    规则面试进程、get_knowledge_context 关键词查找。回复统一按 elevated 口径
    生成（加温备注）——风险 LLM 升级 elevated 时语气恰好正确，low 用户回复
    偏暖（安全偏软方向，产品已确认）。关键约束：**不修改 state**——投机被
    丢弃时，后续节点用真实风险等级重算并注入，不能出现重复注入或提前污染。

    返回 None 表示本条消息不可投机（非 support 模式 / 会诊触发 / 开关关闭）。
    """
    if not _speculation_enabled():
        return None
    from psych_support_bot.ai.consultation import should_trigger_multidisciplinary_consultation
    from psych_support_bot.ai.interview import determine_interview_process
    from psych_support_bot.ai.routers.intent import detect_mode
    from psych_support_bot.ai.tools.knowledge_base import get_knowledge_context

    user_message = state["user_message"]
    if detect_mode(user_message) != "support":
        return None
    # 诊断类关键词在 support 模式也会触发会诊（6 调用路径），投机回复必被
    # 丢弃——直接不投机，省一份 token。
    if should_trigger_multidisciplinary_consultation(user_message=user_message, mode="support", risk_level="elevated"):
        return None

    interview_process = determine_interview_process(
        user_message=user_message,
        mode="support",
        risk_level="elevated",
        turn_count=int(state.get("turn_count") or 0),
    )
    loop_hint = str(interview_process["loop_hint"])
    # 复刻 plan_consultation 的跨轮矛盾提示注入（投机线程内局部拼接，不动 state）
    from psych_support_bot.ai.nodes.consultation_planner import _detect_cross_turn_contradiction

    contradiction = _detect_cross_turn_contradiction(state.get("memory_summary", ""), user_message)
    if contradiction:
        loop_hint = contradiction + " " + loop_hint
    # 复刻 response_generator._inject_refusal_context 的注入语义（局部拼接）
    from psych_support_bot.ai.nodes.response_generator import _anti_repeat_note, _refusal_context_note

    refusal_note = _refusal_context_note(list(state.get("refusal_history") or []))
    if refusal_note:
        loop_hint = refusal_note + " " + loop_hint if loop_hint else refusal_note
    # 复读防线（Langfuse 2026-09-02 c4fd09cc）：投机 prompt 的记忆区含上一轮
    # 回复成品，模型可能整段照抄——提前告知禁止复述。
    if str(state.get("last_bot_reply", "")).strip():
        loop_hint = _anti_repeat_note() + (" " + loop_hint if loop_hint else "")

    return {
        "user_message": user_message,
        "memory_summary": state.get("memory_summary", ""),
        "knowledge_context": get_knowledge_context(mode="support", risk_level="elevated", user_message=user_message),
        "interview_stage": str(interview_process["interview_stage"]),
        "question_strategy": str(interview_process["question_strategy"]),
        "challenge_allowed": bool(interview_process["challenge_allowed"]),
        "loop_hint": loop_hint,
        "expected_language": state.get("expected_language", ""),
        "no_question_mode": bool(state.get("no_question_mode", False)),
    }


def _generate_speculative_reply(args: dict) -> str:
    from psych_support_bot.infra.llm.generation import generate_clinically_bounded_reply

    return generate_clinically_bounded_reply(
        mode="support",
        risk_level="elevated",
        consultation_required=False,
        consultation_agents=[],
        consultation_framework="",
        **args,
    )


def classify_risk(state: GraphState) -> GraphState:
    with trace_span(
        "node.risk_classifier",
        input={"user_message": state["user_message"], "memory_summary": state.get("memory_summary", "")},
    ) as obs:
        risk_result = classify_message_risk(state["user_message"])
        state["risk_result"] = RiskResult(**risk_result.model_dump())

        # LLM 语义兜底：规则判 low/elevated 时二次分类。实验（71 条标注语料）
        # 显示 68% 的危机表述被规则判 low——隐喻（想消失/长眠/遗书）、
        # 变体插入（看不到任何希望）、英文习语；另有死亡委婉表述
        # （"死了才能解脱/没有我会更好"）卡在规则 elevated 词表盲区，
        # 故 elevated 也过 LLM。high/critical 直通（规则升级信号可信）。
        # 单向升级：LLM 只能往上抬；LLM 不可用时维持规则判定
        # （fail-safe，不放大不缩小）。
        #
        # M2 首答延迟优化：风险 LLM 分类与 support 回复生成并行投机。
        # 两个 LLM 调用同时发起（ThreadPoolExecutor + contextvars 快照，
        # 会话轨迹附着复用会诊线程池的既有模式），节点总耗时从
        # risk_llm + reply_llm 串行和降为 max(risk_llm, reply_llm)。
        # 裁决权完整保留：投机回复是否采纳在全部升级逻辑（合并/跨轮/
        # 安全地板）之后决定，升级 high/critical 必然丢弃走危机路径。
        llm_semantic_used = False
        speculative_reply: str | None = None
        if risk_result.risk_level in {"low", "elevated"}:
            spec_args = _prepare_speculative_args(state)

            def _run_risk_llm() -> RiskResult:
                return classify_risk_llm(state["user_message"], state.get("expected_language", ""))

            jobs = [(contextvars.copy_context(), _run_risk_llm)]
            if spec_args is not None:
                jobs.append((contextvars.copy_context(), lambda: _generate_speculative_reply(spec_args)))
            with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
                futures = [executor.submit(job_ctx.run, fn) for job_ctx, fn in jobs]
                try:
                    llm_risk = futures[0].result()
                except Exception:
                    logger.warning(
                        "LLM risk classifier unavailable; keeping rule verdict.",
                        exc_info=True,
                    )
                    llm_risk = None
                if len(futures) > 1:
                    try:
                        speculative_reply = futures[1].result()
                    except Exception:
                        logger.warning(
                            "Speculative reply generation failed; serial path will regenerate.",
                            exc_info=True,
                        )
                        speculative_reply = None

            if llm_risk is not None:
                llm_semantic_used = True
                merged = _merge_upgrade(risk_result, llm_risk)
                if merged.risk_level != risk_result.risk_level:
                    logger.info(
                        "LLM semantic upgrade: %s -> %s (%s)",
                        risk_result.risk_level,
                        merged.risk_level,
                        merged.reason,
                    )
                state["risk_result"] = merged

        # B2.2: Cross-turn risk tracking
        # If the current turn is elevated AND the previous turn was also elevated,
        # automatically upgrade to high risk. Persistent elevated distress across
        # consecutive turns signals accumulating risk that warrants closer attention.
        if risk_result.risk_level == "elevated":
            memory_summary = state.get("memory_summary", "")
            if _has_previous_elevated(memory_summary):
                state["risk_result"] = RiskResult(
                    risk_level="high",
                    risk_types=[*risk_result.risk_types, "cumulative_elevated"],
                    needs_crisis_mode=True,
                    reason=(
                        "Consecutive elevated distress across turns; "
                        "upgraded to high risk for safety. Original: " + risk_result.reason
                    ),
                )
                logger.info("Cross-turn risk upgrade: elevated -> high (consecutive elevated detected)")

        # Safety floor from recent screening results (e.g. a PHQ-9 run whose
        # item-9 answer set needs_safety_followup). A single turn without any
        # matching keyword must not downgrade below a recent clinical signal;
        # severity >= high also arms crisis mode like a direct detection would.
        # NOTE: applied *before* the mode switch below so a floor of high
        # routes into crisis mode exactly like a natively detected signal.
        floor = str(state.get("safety_floor_risk_level") or "").strip()
        if floor in {"elevated", "high", "critical"}:
            severity = {"low": 0, "elevated": 1, "high": 2, "critical": 3}
            current = state["risk_result"]
            if severity[current.risk_level] < severity[floor]:
                state["risk_result"] = RiskResult(
                    risk_level=floor,
                    risk_types=[*current.risk_types, "recent_screening_flag"],
                    needs_crisis_mode=current.needs_crisis_mode or severity[floor] >= 2,
                    reason=(
                        "Safety floor applied: recent screening flagged safety follow-up. Original: " + current.reason
                    ),
                )
                logger.info(
                    "Safety floor applied: %s -> %s (recent screening flag)",
                    current.risk_level,
                    floor,
                )

        if state["risk_result"].needs_crisis_mode:
            state["mode"] = "crisis"

        # M2 投机采纳裁决：合并升级/跨轮升级/安全地板全部走完后，最终风险
        # 仍 ≤ elevated 且未进入危机模式才采用投机回复；否则丢弃置 None，
        # response_generator 按原路径（危机模板 / crisis 软着陆 LLM）生成。
        final_risk = state["risk_result"]
        speculative_adopted = (
            speculative_reply is not None
            and final_risk.risk_level in {"low", "elevated"}
            and not final_risk.needs_crisis_mode
            and state.get("mode") != "crisis"
        )
        state["speculative_reply"] = speculative_reply if speculative_adopted else None

        update_span_output(
            obs,
            {
                "risk_level": state["risk_result"].risk_level,
                "risk_types": state["risk_result"].risk_types,
                "needs_crisis_mode": state["risk_result"].needs_crisis_mode,
                "mode": state["mode"],
                "llm_semantic_used": llm_semantic_used,
                "speculative_reply_adopted": speculative_adopted,
            },
        )
    return state
