import logging
from concurrent.futures import ThreadPoolExecutor

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
    user_is_chinese = expected_language == "zh"
    output_has_chinese = _has_chinese(output)
    output_has_english_words = _contains_ascii_words(output)

    if user_is_chinese and output_has_english_words:
        raise ValueError("Language mismatch: Chinese user input produced English output")
    if not user_is_chinese and output_has_chinese:
        raise ValueError("Language mismatch: English user input produced Chinese output")
    return output


def _invoke(
    system_prompt: str,
    user_message: str,
    expected_language: str,
    *,
    mode: str = "support",
) -> str:
    model = build_chat_model(temperature=get_temperature_for_mode(mode))
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
        response = model.invoke(messages)
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
        return _enforce_language(retry_output, expected_language)


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
    opinion = _invoke(system_prompt, user_message, expected_language, mode=mode)
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
) -> tuple[str, list[dict[str, str]]]:
    expected_language = _expected_language(user_message)
    agents = consultation_agents()
    with ThreadPoolExecutor(max_workers=len(agents)) as executor:
        futures = [
            executor.submit(
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
            )
            for agent in agents
        ]
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
    )
    reply_text = _invoke(synthesis_prompt, user_message, expected_language, mode=mode)
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
) -> str:
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
            ),
            build_context_prompt(
                memory_summary=memory_summary,
                knowledge_context=knowledge_context,
            ),
            build_output_prompt(mode=mode, risk_level=risk_level, user_message=user_message),
        ]
    )
    # Inject diagnosis refusal prompt if user is asking for a diagnosis
    if _is_diagnosis_request(user_message):
        system_prompt = system_prompt + "\n\n" + build_diagnosis_refusal_prompt()
    return _invoke(system_prompt, user_message, expected_language, mode=mode)


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
                "If phase is completed, explain the result plainly using the supplied completion context, mention it is a screening result rather than a diagnosis, and ask one focused follow-up question. "
                "Do not mix Chinese and English in the same answer unless the user explicitly asked for bilingual output."
            ),
        ]
    )
    return _invoke(system_prompt, user_message, expected_language)
