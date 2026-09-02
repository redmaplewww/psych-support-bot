import contextvars
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from langchain_core.messages import HumanMessage, SystemMessage

from psych_support_bot.ai.consultation import consultation_agents
from psych_support_bot.ai.prompts.templates import (
    build_boundary_prompt,
    build_consultation_agent_prompt,
    build_consultation_prompt,
    build_consultation_synthesis_prompt,
    build_context_prompt,
    build_diagnosis_refusal_prompt,
    build_output_prompt,
    build_process_prompt,
    build_role_prompt,
)
from psych_support_bot.ai.routers.intent import DIAGNOSIS_KEYWORDS
from psych_support_bot.ai.utils.text_matching import _contains_keyword, _normalize_text
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.llm.factory import (
    build_chat_model,
    get_temperature_for_mode,
)
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """LLM 在重试后仍不可用，且调用方未提供 fallback。

    所有用 LLM 的路径都必须最终处理这个异常（或提前传入 fallback），
    不允许以裸 500 的形式暴露给处于脆弱状态的用户。
    """


# Retry policy: transient failures (429 / 5xx / timeout / connection) get a
# bounded number of retries with backoff. Deterministic client errors (auth,
# content-safety rejection, bad request) are never retried — they cannot
# succeed on a second attempt.
_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.0)
_NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404, 422}


def _is_retryable_llm_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
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


def _invoke(
    system_prompt: str,
    user_message: str,
    expected_language: str,
    *,
    mode: str = "support",
    fallback: Callable[[], str] | None = None,
) -> str:
    model = build_chat_model(temperature=get_temperature_for_mode(mode), mode=mode)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]
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
        response = None
        last_exc: Exception | None = None
        for attempt in range(len(_RETRY_BACKOFF_SECONDS) + 1):
            try:
                response = model.invoke(messages)
                last_exc = None
                break
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
                    HumanMessage(content=user_message),
                ]
            )
            retry_output = _coerce_content(retry_response.content)
            update_span_output(gen_obs_retry, retry_output)
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
) -> str:
    if not expected_language:
        expected_language = _expected_language(user_message)
    system_prompt = "\n\n".join(
        [
            build_role_prompt(),
            build_boundary_prompt(risk_level=risk_level),
            build_consultation_prompt(
                consultation_required=consultation_required,
                consultation_agents=consultation_agents or [],
                consultation_framework=consultation_framework,
            ),
            build_process_prompt(
                interview_stage=interview_stage,
                question_strategy=question_strategy,
                challenge_allowed=challenge_allowed,
                loop_hint=loop_hint,
                expected_language=expected_language,
                no_question_mode=no_question_mode,
            ),
            build_context_prompt(
                memory_summary=memory_summary,
                knowledge_context=knowledge_context,
            ),
            build_output_prompt(
                mode=mode,
                risk_level=risk_level,
                user_message=user_message,
                expected_language=expected_language,
                no_question_mode=no_question_mode,
            ),
        ]
    )
    # Anti-repeat guard appended last so it sits closest to the output
    # instruction (复读防线，response_generator 传入)。
    if anti_repeat_note:
        system_prompt = system_prompt + "\n\n" + anti_repeat_note
    # Inject diagnosis refusal prompt if user is asking for a diagnosis
    if _is_diagnosis_request(user_message):
        system_prompt = system_prompt + "\n\n" + build_diagnosis_refusal_prompt()
    return _invoke(system_prompt, user_message, expected_language, mode=mode)


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
