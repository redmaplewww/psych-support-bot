import contextvars
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from psych_support_bot.ai.consultation import consultation_agents
from psych_support_bot.ai.prompts.templates import (
    build_boundary_state_prompt,
    build_consultation_agent_prompt,
    build_consultation_synthesis_prompt,
    build_diagnosis_refusal_prompt,
    build_knowledge_block_prompt,
    build_memory_block_prompt,
    build_mode_shape_prompt,
    build_process_state_prompt,
    build_role_prompt,
    build_static_prefix,
)
from psych_support_bot.ai.routers.intent import DIAGNOSIS_KEYWORDS
from psych_support_bot.ai.utils.text_matching import _contains_keyword, _normalize_text
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.llm.factory import (
    build_chat_model,
    get_temperature_for_mode,
)
from psych_support_bot.infra.telemetry.tracing import (
    trace_span,
    update_span_output,
    update_span_usage,
)

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """LLM 在重试后仍不可用，且调用方未提供 fallback。

    所有用 LLM 的路径都必须最终处理这个异常（或提前传入 fallback），
    不允许以裸 500 的形式暴露给处于脆弱状态的用户。
    """


# Retry policy: transient failures (429 / 5xx / timeout / connection) get a
# bounded number of retries with backoff. Deterministic client errors (auth,
# content-safety rejection, bad request) are never retried — they cannot
# succeed on a second attempt. Exception: gateway governance rejections
# (governance.* error types) are observed to fire in bursts on an otherwise
# working key (Langfuse 2026-09-04: 1566 burst 403s), so one retry is allowed.
_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.0)
_NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404, 422}
_GOVERNANCE_STATUS_CODES = {403}


def _is_retryable_llm_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status in _GOVERNANCE_STATUS_CODES and "governance" in str(exc):
            return True
        return status not in _NON_RETRYABLE_STATUS_CODES
    return True


def _is_diagnosis_request(text: str) -> bool:
    normalized, compact = _normalize_text(text)
    return any(_contains_keyword(normalized, compact, kw) for kw in DIAGNOSIS_KEYWORDS)


def _coerce_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(item) for item in content)
    return str(content)


def _has_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _contains_ascii_words(text: str) -> bool:
    import re

    return bool(re.search(r"\b[a-zA-Z]{3,}\b", text))


def _enforce_language(output: str, expected_language: str) -> str:
    """Best-effort language check. Returns warning instead of raising on second failure."""
    user_is_chinese = expected_language == "zh"
    output_has_chinese = _has_chinese(output)
    output_has_english_words = _contains_ascii_words(output)

    if user_is_chinese and output_has_english_words and not output_has_chinese:
        # Output is entirely English when user spoke Chinese — worth retrying
        raise ValueError("Language mismatch: Chinese user input produced English output")
    if not user_is_chinese and output_has_chinese and not output_has_english_words:
        raise ValueError("Language mismatch: English user input produced Chinese output")
    return output


def _history_messages(history: list[dict[str, str]] | None) -> list:
    """逐字近史 → 标准 API 消息序列（user/assistant，按传入顺序）。"""
    out: list = []
    for turn in history or []:
        content = turn.get("content", "")
        if not content:
            continue
        if turn.get("role") == "assistant":
            out.append(AIMessage(content=content))
        else:
            out.append(HumanMessage(content=content))
    return out


def _usage_details(response: object) -> dict[str, int] | None:
    """Map langchain usage_metadata onto Langfuse usage_details keys.

    网关在 prompt_tokens_details 中回传前缀缓存命中，langchain 转换为
    input_token_details.cache_read —— 上报为 input_cached，Langfuse 侧
    即可按调用量化静态前缀的缓存命中率。
    """
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return None
    total = int(usage.get("total_tokens") or 0)
    if not total:
        return None
    details = {
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "total": total,
    }
    cache_read = int((usage.get("input_token_details") or {}).get("cache_read") or 0)
    if cache_read:
        details["input_cached"] = cache_read
    return details


def _invoke(
    system_prompt: str,
    user_message: str,
    expected_language: str,
    *,
    mode: str = "support",
    fallback: Callable[[], str] | None = None,
    user_context: str = "",
    history: list[dict[str, str]] | None = None,
) -> str:
    """LLM 调用咽喉层。

    消息结构（Phase 3 上下文拼接修复，标准 API 格式）：

        [SystemMessage 静态前缀+每轮状态]           ← 前缀字节稳定，缓存命中区
        *[历史轮次 HumanMessage/AssistantMessage]   ← 逐字近史，标准角色
        [HumanMessage 数据区前缀 + 本轮用户消息]     ← 动态信息追加在末尾

    user_context（memory/knowledge 参考数据）作为数据块前缀拼进本轮
    HumanMessage；history 为逐字近史（user/assistant 交替，时间正序），
    让「换个方向吧」这类上下文依赖型消息可被正确解读。
    """
    model = build_chat_model(temperature=get_temperature_for_mode(mode), mode=mode)
    human_content = f"{user_context}\n\n{user_message}" if user_context else user_message
    messages: list = [SystemMessage(content=system_prompt), *_history_messages(history)]
    messages.append(HumanMessage(content=human_content))
    settings = get_settings()
    with trace_span(
        "llm.invoke",
        input={"system_prompt": system_prompt, "user_message": user_message},
        metadata={"model": settings.openai_model, "language": expected_language},
        as_type="generation",
    ) as gen_obs:
        # Single choke point for LLM availability: transient errors are
        # retried with backoff; after exhaustion the caller-declared fallback
        # (if any) is served, otherwise LLMUnavailableError is raised.
        # 空内容视同瞬时失败：dots 网关偶发返回 200+空正文，直接透传会让
        # 用户收到空气泡——与异常同路重试，耗尽后走调用方 fallback/抛错。
        response = None
        last_exc: Exception | None = None
        for attempt in range(len(_RETRY_BACKOFF_SECONDS) + 1):
            try:
                response = model.invoke(messages)
                if _coerce_content(response.content).strip():
                    last_exc = None
                    break
                last_exc = RuntimeError("LLM returned empty content")
                response = None
                if attempt >= len(_RETRY_BACKOFF_SECONDS):
                    break
                logger.warning(
                    "LLM call returned empty content (attempt %d/%d); retrying.",
                    attempt + 1,
                    len(_RETRY_BACKOFF_SECONDS) + 1,
                )
                time.sleep(_RETRY_BACKOFF_SECONDS[attempt])
            except Exception as exc:  # noqa: BLE001 – 咽喉层必须接住所有 LLM 异常
                last_exc = exc
                if attempt >= len(_RETRY_BACKOFF_SECONDS) or not _is_retryable_llm_error(exc):
                    break
                logger.warning(
                    "LLM call failed (attempt %d/%d, retryable): %s",
                    attempt + 1,
                    len(_RETRY_BACKOFF_SECONDS) + 1,
                    exc,
                )
                time.sleep(_RETRY_BACKOFF_SECONDS[attempt])
        if last_exc is not None or response is None:
            update_span_output(gen_obs, {"error": str(last_exc)[:300]})
            if fallback is not None:
                logger.exception("LLM unavailable after retries; serving caller-declared fallback.")
                fallback_text = fallback()
                update_span_output(gen_obs, {"fallback": fallback_text[:300]})
                return fallback_text
            raise LLMUnavailableError(f"LLM unavailable: {last_exc}") from last_exc
        output = _coerce_content(response.content)
        update_span_output(gen_obs, output)
        if (usage := _usage_details(response)) is not None:
            update_span_usage(gen_obs, usage)

    try:
        return _enforce_language(output, expected_language)
    except ValueError:
        retry_prompt = (
            system_prompt
            + "\n\nCRITICAL LANGUAGE RETRY: Your previous answer violated the language lock. "
            + (
                "Rewrite the entire answer in natural Simplified Chinese only. Do not use English words except unavoidable scale names like PHQ-9, GAD-7, or ISI."
                if expected_language == "zh"
                else "Rewrite the entire answer in natural English only. Do not use any Chinese characters."
            )
        )
        with trace_span(
            "llm.invoke_retry",
            input={"system_prompt": retry_prompt, "user_message": user_message},
            metadata={"model": settings.openai_model, "language": expected_language},
            as_type="generation",
        ) as gen_obs_retry:
            retry_response = model.invoke(
                [
                    SystemMessage(content=retry_prompt),
                    *_history_messages(history),
                    HumanMessage(content=human_content),
                ]
            )
            retry_output = _coerce_content(retry_response.content)
            update_span_output(gen_obs_retry, retry_output)
            if (retry_usage := _usage_details(retry_response)) is not None:
                update_span_usage(gen_obs_retry, retry_usage)
        try:
            return _enforce_language(retry_output, expected_language)
        except ValueError:
            # Retry also failed — return as-is rather than crashing with 500
            logger.warning("Language enforcement failed after retry, returning output as-is")
            return retry_output


def _expected_language(user_message: str) -> str:
    return "zh" if _has_chinese(user_message) else "en"


def _generate_consultation_opinion(
    *,
    agent: dict[str, str],
    user_message: str,
    mode: str,
    risk_level: str,
    memory_summary: str,
    knowledge_context: str,
    expected_language: str,
    interview_stage: str,
    question_strategy: str,
    challenge_allowed: bool,
    loop_hint: str,
    conversation_intent: str = "new_request",
) -> dict[str, str]:
    system_prompt = build_consultation_agent_prompt(
        agent_label=agent["label"],
        school=agent["school"],
        focus=agent["focus"],
        memory_summary=memory_summary,
        knowledge_context=knowledge_context,
        mode=mode,
        risk_level=risk_level,
        expected_language=expected_language,
        interview_stage=interview_stage,
        question_strategy=question_strategy,
        challenge_allowed=challenge_allowed,
        loop_hint=loop_hint,
        conversation_intent=conversation_intent,
    )
    fallback_note = (
        f"（{agent['label']}视角暂时不可用。）"
        if expected_language == "zh"
        else f"({agent['label']} perspective temporarily unavailable.)"
    )
    opinion = _invoke(
        system_prompt,
        user_message,
        expected_language,
        mode=mode,
        fallback=lambda: fallback_note,
    )
    return {
        "agent": agent["label"],
        "school": agent["school"],
        "focus": agent["focus"],
        "opinion": opinion,
    }


def generate_multidisciplinary_consultation(
    *,
    user_message: str,
    mode: str,
    risk_level: str,
    memory_summary: str,
    knowledge_context: str,
    consultation_framework: str,
    interview_stage: str,
    question_strategy: str,
    challenge_allowed: bool,
    loop_hint: str,
    expected_language: str = "",
    no_question_mode: bool = False,
    emotional_state: str = "",
    conversation_intent: str = "new_request",
) -> tuple[str, list[dict[str, str]]]:
    if not expected_language:
        expected_language = _expected_language(user_message)
    agents = consultation_agents()
    # ThreadPoolExecutor workers do NOT inherit the caller's contextvars, so
    # without seeding each task with a snapshot of this context the agents'
    # Langfuse spans detach from the current trace and appear as top-level
    # "llm.invoke" traces in the UI. Each job gets its own copy of a context
    # captured here (copies are independent → safe to run concurrently).
    seed_ctx = contextvars.copy_context()

    def _collect_jobs() -> list[tuple[contextvars.Context, partial[dict[str, str]]]]:
        return [
            (
                contextvars.copy_context(),
                partial(
                    _generate_consultation_opinion,
                    agent=agent,
                    user_message=user_message,
                    mode=mode,
                    risk_level=risk_level,
                    memory_summary=memory_summary,
                    knowledge_context=knowledge_context,
                    expected_language=expected_language,
                    interview_stage=interview_stage,
                    question_strategy=question_strategy,
                    challenge_allowed=challenge_allowed,
                    loop_hint=loop_hint,
                    conversation_intent=conversation_intent,
                ),
            )
            for agent in agents
        ]

    jobs = seed_ctx.run(_collect_jobs)
    with ThreadPoolExecutor(max_workers=len(agents)) as executor:
        futures = [executor.submit(job_ctx.run, fn) for job_ctx, fn in jobs]
        opinions = [future.result() for future in futures]

    opinions_text = "\n\n".join(f"[{item['agent']} - {item['school']}]\n{item['opinion']}" for item in opinions)
    synthesis_prompt = build_consultation_synthesis_prompt(
        mode=mode,
        risk_level=risk_level,
        memory_summary=memory_summary,
        knowledge_context=knowledge_context,
        consultation_framework=consultation_framework,
        consultation_opinions=opinions_text,
        user_message=user_message,
        interview_stage=interview_stage,
        question_strategy=question_strategy,
        challenge_allowed=challenge_allowed,
        loop_hint=loop_hint,
        expected_language=expected_language,
        no_question_mode=no_question_mode,
        emotional_state=emotional_state,
        conversation_intent=conversation_intent,
    )

    # Synthesis failure degrades to the raw opinions instead of crashing the
    # whole consultation — the per-agent calls above already succeeded.
    def _synthesis_fallback() -> str:
        zh = expected_language == "zh"
        return (
            f"综合多方会诊意见（{mode}模式，风险 {risk_level}）：\n\n{opinions_text}"
            if zh
            else f"Synthesis of consultation opinions ({mode} mode, risk {risk_level}):\n\n{opinions_text}"
        )

    reply_text = _invoke(
        synthesis_prompt,
        user_message,
        expected_language,
        mode=mode,
        fallback=_synthesis_fallback,
    )
    return reply_text, opinions


def generate_clinically_bounded_reply(
    user_message: str,
    mode: str,
    risk_level: str,
    memory_summary: str,
    knowledge_context: str,
    # consultation_* 仅为签名兼容保留：普通/危机路径恒为 False，真正的多
    # 学科会诊走 generate_multidisciplinary_consultation（旧装配，Phase 5 迁移）。
    consultation_required: bool = False,
    consultation_agents: list[str] | None = None,
    consultation_framework: str = "",
    interview_stage: str = "engagement",
    question_strategy: str = "open",
    challenge_allowed: bool = False,
    loop_hint: str = "Start broad, then narrow.",
    expected_language: str = "",
    no_question_mode: bool = False,
    anti_repeat_note: str = "",
    emotional_state: str = "",
    history: list[dict[str, str]] | None = None,
    conversation_intent: str = "new_request",
) -> str:
    if not expected_language:
        expected_language = _expected_language(user_message)
    # Prompt 分层装配（Phase 1/5）：静态前缀与主回复、会诊 agent、会诊综合
    # 三条路径逐字共享（同一缓存命中池）——网关前缀缓存 512 块粒度，静态区
    # 出现任何每轮插值都会从插值点截断缓存。
    system_prompt = "\n\n".join(
        [
            # --- 静态前缀区（仅随语言分池；部署才变）---
            build_static_prefix(expected_language),
            # --- 每轮状态区（缓存断点之后）：结构化字段，流程驱动 ---
            "## Turn context",
            build_boundary_state_prompt(
                risk_level=risk_level,
                emotional_state=emotional_state,
            ),
            "## Reply shape",
            build_mode_shape_prompt(mode, risk_level, no_question_mode=no_question_mode),
            "## Process frame",
            build_process_state_prompt(
                interview_stage=interview_stage,
                question_strategy=question_strategy,
                challenge_allowed=challenge_allowed,
                loop_hint=loop_hint,
                no_question_mode=no_question_mode,
                conversation_intent=conversation_intent,
            ),
        ]
    )
    # --- 数据区（Phase 2 收尾）：memory/knowledge 作为参考数据前缀进
    # HumanMessage，与用户本轮输入同投递——系统之声纯净，数据贴轮次。
    user_context = "\n\n".join(
        [
            build_memory_block_prompt(memory_summary),
            build_knowledge_block_prompt(knowledge_context),
        ]
    )
    # Anti-repeat guard appended last so it sits closest to the output
    # instruction (复读防线，response_generator 传入)。
    if anti_repeat_note:
        system_prompt = system_prompt + "\n\n" + anti_repeat_note
    # Inject diagnosis refusal prompt if user is asking for a diagnosis
    if _is_diagnosis_request(user_message):
        system_prompt = system_prompt + "\n\n" + build_diagnosis_refusal_prompt()
    return _invoke(
        system_prompt,
        user_message,
        expected_language,
        mode=mode,
        user_context=user_context,
        history=history,
    )


def generate_assessment_history_analysis(
    *,
    history_text: str,
    expected_language: str,
    fallback: Callable[[], str] | None = None,
) -> str:
    """量表历史趋势解读（M1）。

    输入只含动作元数据（日期/量表/分数/严重度），不含情绪叙述内容——
    伦理边界：AI 分析基于分数趋势，不做情绪画像。
    """
    system_prompt = (
        "You are a warm, safety-first assistant summarizing a user's mental-health "
        "screening history for them. The data lines contain date, questionnaire, score, "
        "severity band, and source channel. Rules: speak in the user's language; at most "
        "3 short sentences; describe the trend (improving/worsening/stable/fluctuating) "
        "with concrete numbers; never diagnose, never promise treatment; if the latest "
        "score is moderate or worse, or any trend is worsening, gently mention seeking "
        "professional support; do not mention the source channel in the reply."
    )
    return _invoke(
        system_prompt,
        history_text,
        expected_language,
        mode="support",
        fallback=fallback,
    )


def generate_checkin_trend_analysis(
    *,
    trend_text: str,
    expected_language: str,
    fallback: Callable[[], str] | None = None,
) -> str:
    """打卡数据趋势解读（M2）。

    输入只含数值序列（日期/心情/焦虑/睡眠/精力），不含打卡备注文字——
    伦理边界：AI 分析基于数值规律（睡眠-心情关联、周内波动），
    不做情绪叙述内容画像。
    """
    system_prompt = (
        "You are a warm, safety-first assistant summarizing a user's daily mood check-in "
        "data. Each line has date, mood (0-10), anxiety (0-10), sleep hours, energy (0-10). "
        "Rules: speak in the user's language; at most 3 short sentences; point out one "
        "concrete pattern (e.g. sleep-mood relationship, weekday vs weekend shifts, overall "
        "direction) with numbers; keep it validating, never diagnose, never promise "
        "treatment; if mood is persistently low or anxiety persistently high, gently "
        "mention that professional support can help."
    )
    return _invoke(
        system_prompt,
        trend_text,
        expected_language,
        mode="support",
        fallback=fallback,
    )


def generate_exercise_history_analysis(
    *,
    records_text: str,
    expected_language: str,
    fallback: Callable[[], str] | None = None,
) -> str:
    """练习历史简要分析（M3）。

    输入只含动作元数据（日期/练习类型/来源）——反思笔记内容不进 LLM：
    伦理边界同 M1/M2，AI 分析基于完成频率与类型分布，不做情绪画像。
    """
    system_prompt = (
        "You are a warm, safety-first assistant summarizing a user's self-help exercise "
        "practice history. Each line has date, exercise tag, and source channel. Rules: "
        "speak in the user's language; at most 3 short sentences; note the frequency and "
        "which exercise types they gravitate toward, then suggest one concrete next step "
        "(a different exercise or a repeat of what helped); never diagnose, never promise "
        "treatment; do not mention the source channel in the reply."
    )
    return _invoke(
        system_prompt,
        records_text,
        expected_language,
        mode="support",
        fallback=fallback,
    )


def generate_questionnaire_reply(
    *,
    user_message: str,
    expected_language: str,
    assessment_title: str,
    assessment_code: str,
    phase: str,
    timeframe: str,
    purpose: str,
    instructions: list[str],
    current_index: int,
    total_items: int,
    next_question: str | None,
    options: list[tuple[int, str]],
    answers_so_far: list[int],
    error_hint: str | None = None,
    completion_context: str | None = None,
    fallback: Callable[[], str] | None = None,
) -> str:
    options_text = ", ".join(f"{value} = {label}" for value, label in options)
    system_prompt = "\n\n".join(
        [
            build_role_prompt(),
            (
                "You are guiding the user through a mental health screening questionnaire. "
                "You must not refuse to guide the questionnaire. Do not diagnose. "
                "Be concrete, concise, and interactive."
            ),
            (
                f"Questionnaire: {assessment_title} ({assessment_code}). "
                f"Purpose: {purpose}. Timeframe: {timeframe}. "
                f"Phase: {phase}. Current progress: {current_index}/{total_items}. "
                f"Answers so far: {answers_so_far or 'none'}."
            ),
            (
                f"Instructions: {' '.join(instructions)} "
                f"Next question: {next_question or 'none'}. "
                f"Valid options: {options_text or 'none'}. "
                f"Error hint: {error_hint or 'none'}. "
                f"Completion context: {completion_context or 'none'}."
            ),
            (
                "If phase is start or progress, explain that you will guide one question at a time, "
                "show the current question clearly, list the valid answer options, and explicitly ask the user to answer with one option only. "
                "If phase is invalid_answer, explain what kinds of answers are accepted and then restate the current question. "
                "If phase is skipped, acknowledge the stop briefly without adding fake therapy content. "
                "If phase is completed, summarize the result using the supplied completion context: "
                "always state the numeric score and the severity band, state that this is a screening "
                "result rather than a diagnosis, then ask one focused follow-up question. "
                "The user's last numeric answer is the final questionnaire item, not a refusal to see the result. "
                "Do not mix Chinese and English in the same answer unless the user explicitly asked for bilingual output."
            ),
        ]
    )
    return _invoke(system_prompt, user_message, expected_language, fallback=fallback)
