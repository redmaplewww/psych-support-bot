import json
from typing import Any, cast
from uuid import uuid4

from sqlalchemy.orm import Session

from psych_support_bot.ai.graphs.conversation import conversation_graph
from psych_support_bot.ai.schemas.messages import (
    ConversationMode,
    ConversationRequest,
    ConversationResponse,
    GeneratedReply,
    RiskResult,
)
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.domain.assessments.schemas import AssessmentAnswerSet
from psych_support_bot.domain.assessments.service import (
    build_assessment_followup_reply,
    build_assessment_result,
    build_questionnaire_session_view,
    detect_questionnaire_request,
    detect_skip_or_exit,
    parse_questionnaire_answer,
    questionnaire_guide,
)
from psych_support_bot.infra.db.repositories import (
    append_questionnaire_answer,
    build_memory_snapshot,
    complete_questionnaire_session,
    create_questionnaire_session,
    get_active_questionnaire_session,
    get_session_messages,
    get_user_sessions,
    save_assessment,
    save_conversation_result,
)
from psych_support_bot.infra.llm.generation import generate_questionnaire_reply
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output


def _has_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _is_language_neutral(text: str) -> bool:
    """Return True when the text contains no Chinese characters and no
    ASCII letter words, meaning the language cannot be reliably detected.
    Examples: pure numbers ("3"), punctuation ("..."), single letters ("y")."""
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.isdigit():
        return True
    import re

    has_ascii_words = bool(re.search(r"[a-zA-Z]{3,}", stripped))
    has_chinese = _has_chinese(stripped)
    return not has_chinese and not has_ascii_words


def _detect_expected_language(
    current_message: str,
    prior_messages: list[Any] | None = None,
) -> str:
    """Determine the expected conversation language.

    If the current message contains enough linguistic signal (Chinese
    characters or ASCII words), use it directly. Otherwise, walk
    backwards through *prior_messages* (role == 'user') to find the
    most recent message with clear language signal and inherit its
    language. Falls back to 'en' when nothing is found.
    """
    if not _is_language_neutral(current_message):
        return "zh" if _has_chinese(current_message) else "en"

    if prior_messages:
        for msg in reversed(prior_messages):
            if getattr(msg, "role", None) != "user":
                continue
            content = getattr(msg, "content", "")
            if _is_language_neutral(content):
                continue
            return "zh" if _has_chinese(content) else "en"

    return "en"


class ConversationService:
    def _build_response(
        self,
        *,
        session_id: str,
        mode: ConversationMode,
        reply_text: str,
        summary: str,
        risk_level: str = "low",
        risk_reason: str = "No obvious high-risk language detected.",
        debug: dict[str, object] | None = None,
    ) -> ConversationResponse:
        return ConversationResponse(
            session_id=session_id,
            mode=mode,
            risk=RiskResult(
                risk_level=cast(Any, risk_level),
                risk_types=[],
                needs_crisis_mode=risk_level in {"high", "critical"},
                reason=risk_reason,
            ),
            reply=GeneratedReply(
                text=reply_text,
                style=mode,
                includes_action_step=True,
            ),
            summary=summary,
            debug=debug or {},
        )

    def _is_first_message(self, payload: ConversationRequest, session: Session) -> bool:
        sessions = get_user_sessions(session, payload.user_id, limit=1)
        return len(sessions) == 0

    def _handle_questionnaire_flow(
        self,
        payload: ConversationRequest,
        session: Session,
    ) -> ConversationResponse | None:
        def _questionnaire_reply(
            *,
            user_message: str,
            expected_language: str,
            guide: Any,
            phase: str,
            current_index: int,
            total_items: int,
            next_question: str | None,
            options: list[tuple[int, str]],
            answers_so_far: list[int],
            error_hint: str | None = None,
            completion_context: str | None = None,
        ) -> str:
            return generate_questionnaire_reply(
                user_message=user_message,
                expected_language=expected_language,
                assessment_title=guide.title,
                assessment_code=guide.code,
                phase=phase,
                timeframe=guide.timeframe,
                purpose=guide.purpose,
                instructions=guide.instructions,
                current_index=current_index,
                total_items=total_items,
                next_question=next_question,
                options=options,
                answers_so_far=answers_so_far,
                error_hint=error_hint,
                completion_context=completion_context,
            )

        active_session = get_active_questionnaire_session(session, payload.user_id)
        if active_session is not None:
            assessment_type = cast(Any, active_session.assessment_type)
            answer_value = parse_questionnaire_answer(payload.message, assessment_type)
            answers = cast(list[int], json.loads(active_session.answers_json or "[]"))
            prior_messages = get_session_messages(session, active_session.id)
            expected_language = _detect_expected_language(payload.message, prior_messages)
            guide = questionnaire_guide(assessment_type, language=expected_language)
            view = build_questionnaire_session_view(
                session_id=active_session.id,
                user_id=active_session.user_id,
                assessment_type=assessment_type,
                answers=answers,
                status=active_session.status,
                language=expected_language,
            )

            if answer_value is None:
                skip_exit = detect_skip_or_exit(payload.message)
                if skip_exit:
                    completed = complete_questionnaire_session(session, active_session)
                    return self._build_response(
                        session_id=completed.id,
                        mode="assessment",
                        reply_text=_questionnaire_reply(
                            user_message=payload.message,
                            expected_language=expected_language,
                            guide=guide,
                            phase="skipped",
                            current_index=view.current_index,
                            total_items=view.total_items,
                            next_question=(view.next_item.text if view.next_item is not None else None),
                            options=[
                                (option.value, option.label)
                                for option in (view.next_item.options if view.next_item else [])
                            ],
                            answers_so_far=answers,
                        ),
                        summary=f"Questionnaire {assessment_type} skipped by user.",
                        debug={
                            "source": "questionnaire_skip",
                            "llm_used": True,
                            "fallback_used": False,
                            "assessment_type": assessment_type,
                        },
                    )
                is_chinese = expected_language == "zh"
                max_hint = 4 if assessment_type == "isi" else 3
                if is_chinese:
                    error_hint = f"请回复一个数字（0到{max_hint}之间），对应你的感受。"
                else:
                    error_hint = f"Please reply with a number (0 through {max_hint}) matching your experience."
                reply_text = _questionnaire_reply(
                    user_message=payload.message,
                    expected_language=expected_language,
                    guide=guide,
                    phase="invalid_answer",
                    current_index=view.current_index + 1,
                    total_items=view.total_items,
                    next_question=(view.next_item.text if view.next_item is not None else None),
                    options=[
                        (option.value, option.label) for option in (view.next_item.options if view.next_item else [])
                    ],
                    answers_so_far=answers,
                    error_hint=error_hint,
                )
                return self._build_response(
                    session_id=active_session.id,
                    mode="assessment",
                    reply_text=reply_text,
                    summary=f"Questionnaire {assessment_type} still in progress.",
                    debug={
                        "source": "questionnaire_progress",
                        "llm_used": True,
                        "fallback_used": False,
                        "assessment_type": assessment_type,
                    },
                )

            updated = append_questionnaire_answer(session, active_session, answer_value)
            updated_answers = cast(list[int], json.loads(updated.answers_json or "[]"))
            updated_view = build_questionnaire_session_view(
                session_id=updated.id,
                user_id=updated.user_id,
                assessment_type=assessment_type,
                answers=updated_answers,
                status=updated.status,
                language=expected_language,
            )
            if updated_view.next_item is not None:
                return self._build_response(
                    session_id=updated.id,
                    mode="assessment",
                    reply_text=_questionnaire_reply(
                        user_message=payload.message,
                        expected_language=expected_language,
                        guide=guide,
                        phase="progress",
                        current_index=updated_view.current_index + 1,
                        total_items=updated_view.total_items,
                        next_question=updated_view.next_item.text,
                        options=[(option.value, option.label) for option in updated_view.next_item.options],
                        answers_so_far=updated_answers,
                    ),
                    summary=(
                        f"Questionnaire {assessment_type} progress "
                        f"{updated_view.current_index}/{updated_view.total_items}."
                    ),
                    debug={
                        "source": "questionnaire_progress",
                        "llm_used": True,
                        "fallback_used": False,
                        "assessment_type": assessment_type,
                    },
                )

            completed = complete_questionnaire_session(session, updated)
            result = build_assessment_result(
                assessment_type,
                answers=AssessmentAnswerSet(answers=updated_answers),
                language=expected_language,
            )
            save_assessment(session, completed.user_id, result)
            risk_level = "elevated" if result.interpretation.needs_safety_followup else "low"
            risk_reason = (
                "Assessment safety follow-up recommended."
                if result.interpretation.needs_safety_followup
                else "Assessment completed without urgent safety signal."
            )
            return self._build_response(
                session_id=completed.id,
                mode="assessment",
                reply_text=_questionnaire_reply(
                    user_message=payload.message,
                    expected_language=expected_language,
                    guide=guide,
                    phase="completed",
                    current_index=len(updated_answers),
                    total_items=len(updated_answers),
                    next_question=None,
                    options=[],
                    answers_so_far=updated_answers,
                    completion_context=build_assessment_followup_reply(result, user_message=payload.message),
                ),
                summary=(
                    f"Completed questionnaire {assessment_type} with score {result.score} ({result.severity_band})."
                ),
                risk_level=risk_level,
                risk_reason=risk_reason,
                debug={
                    "source": "assessment_result",
                    "llm_used": True,
                    "fallback_used": False,
                    "assessment_type": assessment_type,
                    "assessment_score": result.score,
                },
            )

        requested = detect_questionnaire_request(payload.message)
        if requested is None:
            return None

        record = create_questionnaire_session(session, payload.user_id, requested)
        expected_language = _detect_expected_language(payload.message)
        guide = questionnaire_guide(requested, language=expected_language)
        view = build_questionnaire_session_view(
            session_id=record.id,
            user_id=record.user_id,
            assessment_type=requested,
            answers=[],
            status=record.status,
            language=expected_language,
        )
        return self._build_response(
            session_id=record.id,
            mode="assessment",
            reply_text=_questionnaire_reply(
                user_message=payload.message,
                expected_language=expected_language,
                guide=guide,
                phase="start",
                current_index=1,
                total_items=view.total_items,
                next_question=(view.next_item.text if view.next_item is not None else None),
                options=[(option.value, option.label) for option in (view.next_item.options if view.next_item else [])],
                answers_so_far=[],
            ),
            summary=f"Started questionnaire {requested}.",
            debug={
                "source": "assessment_start",
                "llm_used": True,
                "fallback_used": False,
                "assessment_type": requested,
            },
        )

    def respond(
        self,
        payload: ConversationRequest,
        session: Session,
    ) -> ConversationResponse:
        questionnaire_response = self._handle_questionnaire_flow(payload, session)
        if questionnaire_response is not None:
            save_conversation_result(
                session=session,
                response=questionnaire_response,
                user_message=payload.message,
                user_id=payload.user_id,
            )
            return questionnaire_response

        session_id = payload.session_id or str(uuid4())
        memory_summary = payload.memory_summary or build_memory_snapshot(session, payload.user_id)

        # Determine the expected language from the current message and,
        # when the message is language-neutral (e.g. pure numbers), from
        # prior conversation history so the language stays consistent.
        prior_messages = get_session_messages(session, session_id) if payload.session_id else []
        expected_language = _detect_expected_language(payload.message, prior_messages)

        state: GraphState = {
            "user_id": payload.user_id,
            "session_id": session_id,
            "user_message": payload.message,
            "memory_summary": memory_summary,
            "knowledge_context": "",
            "mode": "support",
            "risk_result": RiskResult(
                risk_level="low",
                risk_types=[],
                needs_crisis_mode=False,
                reason="",
            ),
            "generated_reply": GeneratedReply(
                text="",
                style="support",
                includes_action_step=True,
            ),
            "session_summary": "",
            "topics": [],
            "fallback_used": False,
            "consultation_required": False,
            "consultation_agents": [],
            "consultation_notes": "",
            "consultation_opinions": [],
            "interview_stage": "engagement",
            "question_strategy": "open",
            "challenge_allowed": False,
            "loop_hint": "Start with broad exploration before narrowing.",
            "exercise_history": [],
            "refusal_history": [],
            "expected_language": expected_language,
        }
        with trace_span(
            "conversation_graph.invoke",
            input={
                "user_id": payload.user_id,
                "session_id": session_id,
                "message": payload.message,
                "mode": "support",
            },
            metadata={"memory_summary": memory_summary},
        ) as root_obs:
            raw_result = cast(Any, conversation_graph.invoke(cast(Any, state)))
        result: GraphState = cast(GraphState, raw_result)
        response = ConversationResponse(
            session_id=session_id,
            mode=result["mode"],
            risk=result["risk_result"],
            reply=result["generated_reply"],
            summary=result["session_summary"],
            debug={
                "source": "graph",
                "llm_used": not bool(result.get("fallback_used")),
                "fallback_used": bool(result.get("fallback_used")),
                "knowledge_chars": len(result.get("knowledge_context", "")),
                "memory_chars": len(result.get("memory_summary", "")),
                "topics": result.get("topics", []),
                "consultation_required": bool(result.get("consultation_required", False)),
                "consultation_agents": result.get("consultation_agents", []),
                "consultation_notes": result.get("consultation_notes", ""),
                "consultation_opinions": result.get("consultation_opinions", []),
                "interview_stage": result.get("interview_stage", "engagement"),
                "question_strategy": result.get("question_strategy", "open"),
                "challenge_allowed": bool(result.get("challenge_allowed", False)),
                "loop_hint": result.get("loop_hint", "Start with broad exploration before narrowing."),
                "exercise_history": result.get("exercise_history", []),
                "refusal_history": result.get("refusal_history", []),
            },
        )
        update_span_output(
            root_obs,
            {
                "mode": result["mode"],
                "risk_level": result["risk_result"].risk_level,
                "reply": result["generated_reply"].text,
                "summary": result["session_summary"],
            },
        )
        save_conversation_result(
            session=session,
            response=response,
            user_message=payload.message,
            user_id=payload.user_id,
        )
        return response


conversation_service = ConversationService()
