import json
import logging
from datetime import UTC, datetime
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
from psych_support_bot.ai.tools.exercises import detect_completed_exercise
from psych_support_bot.domain.assessments.schemas import AssessmentAnswerSet
from psych_support_bot.domain.assessments.service import (
    build_assessment_followup_reply,
    build_assessment_result,
    build_progress_prefix,
    build_questionnaire_session_view,
    classify_disengage,
    cooldown_days_for,
    detect_questionnaire_request,
    detect_retest_override,
    detect_skip_or_exit,
    format_trend_line,
    parse_questionnaire_answer,
    questionnaire_guide,
)
from psych_support_bot.infra.db.exercise_repositories import save_exercise_record
from psych_support_bot.infra.db.repositories import (
    append_questionnaire_answer,
    build_memory_snapshot,
    complete_questionnaire_session,
    create_questionnaire_session,
    get_active_questionnaire_session,
    get_latest_assessment,
    get_paused_questionnaire_session,
    get_session_messages,
    get_user_sessions,
    pause_questionnaire_session,
    resume_questionnaire_session,
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


_SAFETY_RESOURCES_ZH = (
    "如果你此刻感到很难受，请记得随时可以拨打全国心理援助热线 400-161-9995（24 小时），"
    "紧急情况请直接拨打 120。你不必独自扛着这些。"
)
_SAFETY_RESOURCES_EN = (
    "If things feel heavy right now, the 988 Suicide & Crisis Lifeline (call or text 988) "
    "is available around the clock, and in an emergency please call 911. You don't have to carry this alone."
)


def _options_payload(options: Any) -> list[dict[str, Any]]:
    return [{"value": option.value, "label": option.label} for option in (options or [])]


def _days_since(dt: datetime) -> int:
    """Whole days since *dt*, tolerating both naive and aware timestamps
    (SQLite round-trips drop tzinfo)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return max((datetime.now(UTC) - dt).days, 0)


# How long a screening result with needs_safety_followup keeps enforcing the
# elevated-risk floor on every incoming message.
SAFETY_FLOOR_WINDOW_DAYS = 7


def _count_recent_invalid_answers(prior_messages: list[Any], assessment_type: str) -> int:
    """Count consecutive trailing user messages that failed answer parsing."""
    misses = 0
    for record in reversed(prior_messages):
        if getattr(record, "role", "") != "user":
            continue
        if parse_questionnaire_answer(getattr(record, "content", ""), assessment_type) is not None:
            break
        misses += 1
        if misses >= 3:
            break
    return misses


logger = logging.getLogger(__name__)


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
        question_options: list[dict[str, Any]] | None = None,
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
            question_options=question_options or [],
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
            zh = expected_language == "zh"

            def _deterministic_question_body() -> str:
                options_text = (
                    "，".join(f"{value}={label}" for value, label in options)
                    if zh
                    else ", ".join(f"{value} = {label}" for value, label in options)
                )
                body = next_question or ""
                if options_text:
                    body += f"（{options_text}）" if zh else f" ({options_text})"
                if error_hint:
                    body += f" {error_hint}"
                return body

            # 上游 LLM 不可用（限流/内容安全拦截/网络故障）时的确定性降级，
            # 经 _invoke 咽喉层声明调用——问卷流程不允许崩给用户。
            # Langfuse 巡检（2026-08-23）发现该路径 LLM 403 会直接 500。
            def deterministic_fallback() -> str:
                if phase == "completed":
                    return completion_context or (
                        f"{guide.title}已完成，感谢你的作答。"
                        if zh
                        else f"{guide.title} is complete. Thank you for answering."
                    )
                return (
                    build_progress_prefix(guide.title, current_index, total_items, expected_language)
                    + _deterministic_question_body()
                )

            # 题目呈现完全确定性（start/progress/invalid_answer/resumed）：题干、
            # 选项、进度全部来自状态机，LLM 不参与。此前这些轮次正文由 LLM 生成
            # 且无输出校验——ISI 第 7/7 题事故（2026-09-01）：LLM 在 progress 轮
            # 幻觉出「完成总结 + 编造分数」，题干缺失、选项悬空。临床工具的问卷
            # 完整性不允许交给采样。LLM 仅用于 completed 轮的结果转述（有兜底）。
            if phase != "completed":
                return _deterministic_question_body()
            if phase == "completed" and not next_question and options:
                # 防御：completed 轮不应携带题目数据
                options = []

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
                fallback=deterministic_fallback,
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
                disengage = classify_disengage(payload.message)
                if disengage in {"pause", "quiet"}:
                    # "安静待会儿" during a questionnaire also parks it — pressing
                    # for numbers after that would be the worst possible reply.
                    pause_questionnaire_session(session, active_session)
                    zh_pause = expected_language == "zh"
                    if disengage == "quiet":
                        tip = (
                            f"好，{guide.title}就放到这里，已答的 {len(answers)} 题都保存了。"
                            "你想静静的话，我在旁边陪着，不问任何问题；想继续的时候说一声就行。"
                            if zh_pause
                            else (
                                f"Of course — we'll leave the {guide.title} here; your {len(answers)} "
                                "answers are saved. I'll keep you company quietly, no questions. "
                                "Just say the word whenever you want to continue."
                            )
                        )
                    else:
                        tip = (
                            f"好，{guide.title}先放在这里。已作答的 {len(answers)} 题都保存了，"
                            f"之后想继续时说一声「继续{guide.title}」就行。现在想做点别的也可以。"
                            if zh_pause
                            else (
                                f"Sure, we'll leave the {guide.title} here. Your {len(answers)} answered "
                                "items are saved — just ask to continue whenever you're ready, "
                                "or talk about something else for now."
                            )
                        )
                    return self._build_response(
                        session_id=active_session.id,
                        mode="assessment",
                        reply_text=tip,
                        summary=f"Questionnaire {assessment_type} paused at item {len(answers)}.",
                        debug={
                            "source": "questionnaire_paused",
                            "llm_used": False,
                            "fallback_used": False,
                            "assessment_type": assessment_type,
                        },
                    )
                skip_exit = detect_skip_or_exit(payload.message)
                if skip_exit:
                    completed = complete_questionnaire_session(session, active_session)
                    zh_skip = expected_language == "zh"
                    exit_reply = (
                        f"好，{guide.title}就先到这里，这次作答不计入结果。"
                        "想重新测的时候说一声就行；或者直接跟我聊聊现在的感受也可以。"
                        if zh_skip
                        else (
                            f"Sure, we'll leave the {guide.title} here — this attempt won't be scored. "
                            "Say the word whenever you want to restart, or just tell me how you're feeling."
                        )
                    )
                    return self._build_response(
                        session_id=completed.id,
                        mode="assessment",
                        reply_text=exit_reply,
                        summary=f"Questionnaire {assessment_type} skipped by user.",
                        debug={
                            "source": "questionnaire_skip",
                            "llm_used": False,
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
                misses = _count_recent_invalid_answers(prior_messages, assessment_type)
                if misses >= 2:
                    if is_chinese:
                        error_hint += " 如果暂时不想测了，回复「暂停」可以保存进度稍后再来；有其他想聊的也直接说。"
                    else:
                        error_hint += (
                            ' If you\'d rather stop for now, reply "pause" and your progress will be saved; '
                            "you can also just tell me what's on your mind."
                        )
                reply_text = build_progress_prefix(
                    guide.title, view.current_index + 1, view.total_items, expected_language
                ) + _questionnaire_reply(
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
                    question_options=_options_payload(view.next_item.options if view.next_item else []),
                    debug={
                        "source": "questionnaire_progress",
                        "llm_used": False,
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
                reply_text = build_progress_prefix(
                    guide.title, updated_view.current_index + 1, updated_view.total_items, expected_language
                ) + _questionnaire_reply(
                    user_message=payload.message,
                    expected_language=expected_language,
                    guide=guide,
                    phase="progress",
                    current_index=updated_view.current_index + 1,
                    total_items=updated_view.total_items,
                    next_question=updated_view.next_item.text,
                    options=[(option.value, option.label) for option in updated_view.next_item.options],
                    answers_so_far=updated_answers,
                )
                return self._build_response(
                    session_id=updated.id,
                    mode="assessment",
                    reply_text=reply_text,
                    summary=(
                        f"Questionnaire {assessment_type} progress "
                        f"{updated_view.current_index}/{updated_view.total_items}."
                    ),
                    question_options=_options_payload(updated_view.next_item.options),
                    debug={
                        "source": "questionnaire_progress",
                        "llm_used": False,
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
            # Capture the previous run BEFORE saving this one so trend
            # comparison refers to the prior attempt, not the current.
            previous = get_latest_assessment(session, completed.user_id, assessment_type)
            save_assessment(session, completed.user_id, result)
            risk_level = "elevated" if result.interpretation.needs_safety_followup else "low"
            risk_reason = (
                "Assessment safety follow-up recommended."
                if result.interpretation.needs_safety_followup
                else "Assessment completed without urgent safety signal."
            )
            completion_context = build_assessment_followup_reply(result, user_message=payload.message)
            if result.interpretation.needs_safety_followup:
                # PHQ-9 item 9 endorsed: lead the completion message with care
                # and crisis resources instead of burying them in the summary.
                resources = _SAFETY_RESOURCES_ZH if expected_language == "zh" else _SAFETY_RESOURCES_EN
                completion_context = f"{resources} {completion_context}"
            if previous is not None:
                completion_context += " " + format_trend_line(
                    expected_language,
                    prev_score=previous.score,
                    days_since=_days_since(previous.created_at),
                    new_score=result.score,
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
                    completion_context=completion_context,
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

        expected_language = _detect_expected_language(payload.message)
        guide = questionnaire_guide(requested, language=expected_language)
        zh = expected_language == "zh"

        paused = get_paused_questionnaire_session(session, payload.user_id, requested)
        if paused is not None:
            resume_questionnaire_session(session, paused)
            answers = cast(list[int], json.loads(paused.answers_json or "[]"))
            view = build_questionnaire_session_view(
                session_id=paused.id,
                user_id=paused.user_id,
                assessment_type=requested,
                answers=answers,
                status="in_progress",
                language=expected_language,
            )
            reply_text = build_progress_prefix(
                guide.title, view.current_index + 1, view.total_items, expected_language
            ) + _questionnaire_reply(
                user_message=payload.message,
                expected_language=expected_language,
                guide=guide,
                phase="resumed",
                current_index=view.current_index + 1,
                total_items=view.total_items,
                next_question=(view.next_item.text if view.next_item is not None else None),
                options=[(option.value, option.label) for option in (view.next_item.options if view.next_item else [])],
                answers_so_far=answers,
            )
            return self._build_response(
                session_id=paused.id,
                mode="assessment",
                reply_text=reply_text,
                summary=f"Resumed questionnaire {requested} at item {len(answers)}.",
                question_options=_options_payload(view.next_item.options if view.next_item else []),
                debug={
                    "source": "assessment_resumed",
                    "llm_used": False,
                    "fallback_used": False,
                    "assessment_type": requested,
                },
            )

        recent = get_latest_assessment(session, payload.user_id, requested)
        if recent is not None and not detect_retest_override(payload.message):
            days_since = _days_since(recent.created_at)
            if days_since < cooldown_days_for(requested):
                tip = (
                    f"你在 {days_since} 天前刚做过{guide.title}，当时的得分是 {recent.score} 分"
                    f"（{recent.severity_band}）。一周内重复施测分数波动较大，参考意义有限。"
                    f"如果想看变化趋势，建议过几天再来。当然，如果你确实想现在重新测一遍，回复「重新测」即可开始；"
                    f"或者直接跟我聊聊最近的状态也可以。"
                    if zh
                    else (
                        f"You completed the {guide.title} {days_since} day(s) ago, scoring {recent.score} "
                        f"({recent.severity_band}). Retaking within a week tends to produce unstable scores. "
                        'If you\'d still like to redo it now, just say "retake"; otherwise feel free to '
                        "tell me how you've been lately."
                    )
                )
                return self._build_response(
                    session_id=payload.session_id or str(uuid4()),
                    mode="assessment",
                    reply_text=tip,
                    summary=f"{requested} retest declined by cooldown ({days_since}d).",
                    debug={
                        "source": "assessment_cooldown",
                        "llm_used": False,
                        "fallback_used": False,
                        "assessment_type": requested,
                    },
                )

        record = create_questionnaire_session(session, payload.user_id, requested)
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
            reply_text=build_progress_prefix(guide.title, 1, view.total_items, expected_language)
            + _questionnaire_reply(
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
            question_options=_options_payload(view.next_item.options if view.next_item else []),
            debug={
                "source": "assessment_start",
                "llm_used": False,
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
            # Quiet mode: honor "别问了/让我静静" by suppressing questions this
            # turn; summary_writer persists the preference for later turns.
            "no_question_mode": classify_disengage(payload.message) == "quiet",
            # Depth of this conversation; feeds stage-floor escalation.
            "turn_count": len(prior_messages),
            # A recent flagged screening (PHQ-9 item 9 etc.) raises the risk
            # floor so quiet/ambiguous turns still land in the safety path.
            "safety_floor_risk_level": (
                "elevated"
                if (
                    (recent_screening := get_latest_assessment(session, payload.user_id, "phq9")) is not None
                    and recent_screening.needs_safety_followup
                    and _days_since(recent_screening.created_at) <= SAFETY_FLOOR_WINDOW_DAYS
                )
                else ""
            ),
            "expected_language": expected_language,
            # 最近一条 bot 回复：response_generator 用它做逐字重复检测。
            "last_bot_reply": next(
                (m.content for m in reversed(prior_messages) if getattr(m, "role", "") == "assistant"),
                "",
            ),
            # M2 投机并行：risk_classifier 决定是否填充（None=无投机）。
            "speculative_reply": None,
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
            session_id=session_id,
            user_id=payload.user_id,
        ) as root_obs:
            try:
                raw_result = cast(Any, conversation_graph.invoke(cast(Any, state)))
            except Exception:
                # 最后一道防线：graph 内部任何未捕获异常（LLM 故障、节点 bug）
                # 都不能以 500 形式暴露给处于脆弱状态的用户。
                # Langfuse 巡检（2026-08-23）：越狱输入触发上游 403 后
                # graph 输出为空，用户端收到错误响应。
                logger.exception("Conversation graph failed; serving static safety fallback reply.")
                update_span_output(root_obs, {"error": "graph_invoke_failed", "fallback": True})
                fallback_zh = expected_language == "zh"
                reply_text = (
                    "我在这里陪你。刚刚我这边遇到了一点技术问题，没能好好回应你，"
                    "但你的感受很重要。如果你现在感到不安全，请立即拨打120，"
                    "或联系一位信任的人陪在你身边。"
                    if fallback_zh
                    else (
                        "I am here with you. I just hit a technical problem and could not respond properly, "
                        "but your feelings matter. If you feel unsafe right now, please call emergency "
                        "services or reach out to someone you trust."
                    )
                )
                fallback_response = ConversationResponse(
                    session_id=session_id,
                    mode="support",
                    risk=RiskResult(
                        risk_level="low",
                        risk_types=[],
                        needs_crisis_mode=False,
                        reason="Graph failure fallback.",
                    ),
                    reply=GeneratedReply(text=reply_text, style="support", includes_action_step=True),
                    summary="Graph invocation failed; static safety fallback served.",
                    debug={
                        "source": "graph_fallback",
                        "llm_used": False,
                        "fallback_used": True,
                    },
                )
                save_conversation_result(
                    session=session,
                    response=fallback_response,
                    user_message=payload.message,
                    user_id=payload.user_id,
                )
                return fallback_response
            done_state: GraphState = cast(GraphState, raw_result)
            # Root-level output so the Langfuse UI shows a usable summary row
            # per conversation turn instead of a null output.
            update_span_output(
                root_obs,
                {
                    "session_id": session_id,
                    "mode": done_state["mode"],
                    "risk_level": done_state["risk_result"].risk_level,
                    "reply_text": done_state["generated_reply"].text[:200],
                },
            )
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
        # NOTE: root trace output/session fields are written inside the
        # trace_span block above; the span is already closed here, so any
        # update after it would be silently dropped.
        save_conversation_result(
            session=session,
            response=response,
            user_message=payload.message,
            user_id=payload.user_id,
        )
        # M3 对话图联动：对话中完成练习时自动落库（exercise_history 之前只
        # 存在于图状态的内存字段，现在持久化）。识别不到不记，宁漏不误。
        completed_tag = detect_completed_exercise(payload.message)
        if completed_tag:
            save_exercise_record(session, payload.user_id, completed_tag, source="chat")
        return response


conversation_service = ConversationService()
