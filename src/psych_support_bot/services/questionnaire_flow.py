"""问卷会话内状态机（P3 自 services/conversation.py 拆出，行为逐字保留）。

分支布局：
- 进行中会话：暂停（pause/quiet）、放弃（skip/exit）、情绪倾诉自动暂停、
  无效作答重推（含连续 2 次未答的软退出提示）、有效作答 → 下一题或完成结算
- 无进行中会话：非问卷请求放行（None）、暂停恢复、冷却期拦截、新开

响应组装经 build_response 回调（ConversationService._build_response），
本模块不持有服务实例，避免双向依赖。
"""

import json
from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

from sqlalchemy.orm import Session

from psych_support_bot.ai.routers.intent import CRISIS_KEYWORDS
from psych_support_bot.ai.safety.crisis import build_crisis_reply
from psych_support_bot.ai.schemas.messages import ConversationRequest, ConversationResponse, RiskResult
from psych_support_bot.domain.assessments.schemas import AssessmentAnswerSet
from psych_support_bot.domain.assessments.service import (
    build_assessment_followup_reply,
    build_assessment_result,
    build_progress_prefix,
    build_questionnaire_session_view,
    classify_disengage,
    cooldown_days_for,
    detect_emotional_disclosure,
    detect_questionnaire_request,
    detect_retest_override,
    detect_skip_or_exit,
    format_trend_line,
    parse_questionnaire_answer,
    questionnaire_guide,
)
from psych_support_bot.infra.db.repositories import (
    append_questionnaire_answer,
    complete_questionnaire_session,
    create_questionnaire_session,
    get_active_questionnaire_session,
    get_latest_assessment,
    get_paused_questionnaire_session,
    get_session_messages,
    pause_questionnaire_session,
    resume_questionnaire_session,
    save_assessment,
)
from psych_support_bot.infra.llm.generation import generate_questionnaire_reply
from psych_support_bot.services.support import _days_since, _detect_expected_language

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
                f"{guide.title}已完成，感谢你的作答。" if zh else f"{guide.title} is complete. Thank you for answering."
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


class QuestionnaireFlow:
    """问卷会话状态机。build_response 注入 ConversationService._build_response。"""

    def __init__(self, build_response: Callable[..., ConversationResponse]) -> None:
        self._build_response = build_response

    def handle(
        self,
        payload: ConversationRequest,
        session: Session,
    ) -> ConversationResponse | None:
        active_session = get_active_questionnaire_session(session, payload.user_id)
        if active_session is not None:
            return self._handle_active_session(payload, session, active_session)

        requested = detect_questionnaire_request(payload.message)
        if requested is None:
            return None
        return self._handle_new_request(payload, session, requested)

    # ------------------------------------------------------------------
    # 进行中会话：作答处理
    # ------------------------------------------------------------------

    def _handle_active_session(
        self,
        payload: ConversationRequest,
        session: Session,
        active_session: Any,
    ) -> ConversationResponse:
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
                return self._pause_for_disengage(
                    session, active_session, guide, assessment_type, answers, expected_language, disengage
                )
            skip_exit = detect_skip_or_exit(payload.message)
            if skip_exit:
                return self._exit_on_skip(session, active_session, guide, assessment_type, expected_language)
            return self._handle_emotional_or_invalid(
                payload,
                session,
                active_session,
                guide,
                assessment_type,
                answers,
                view,
                prior_messages,
                expected_language,
            )

        return self._handle_valid_answer(
            payload, session, active_session, guide, assessment_type, answer_value, expected_language
        )

    def _pause_for_disengage(
        self,
        session: Session,
        active_session: Any,
        guide: Any,
        assessment_type: str,
        answers: list[int],
        expected_language: str,
        disengage: str,
    ) -> ConversationResponse:
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

    def _exit_on_skip(
        self,
        session: Session,
        active_session: Any,
        guide: Any,
        assessment_type: str,
        expected_language: str,
    ) -> ConversationResponse:
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

    def _handle_emotional_or_invalid(
        self,
        payload: ConversationRequest,
        session: Session,
        active_session: Any,
        guide: Any,
        assessment_type: str,
        answers: list[int],
        view: Any,
        prior_messages: list[Any],
        expected_language: str,
    ) -> ConversationResponse:
        # Emotional disclosure mid-questionnaire (Langfuse 巡检 2026-09-04:
        # 「我最近还感到很焦虑」被反复回以「请回复一个数字」)。危机信号走
        # 危机引导；普通情绪倾诉自动暂停问卷、转回倾听——推题永远排在
        # 人的感受之后。
        if detect_emotional_disclosure(payload.message):
            return self._pause_on_emotional_disclosure(
                session, active_session, guide, assessment_type, answers, payload.message, expected_language
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
            options=[(option.value, option.label) for option in (view.next_item.options if view.next_item else [])],
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

    def _pause_on_emotional_disclosure(
        self,
        session: Session,
        active_session: Any,
        guide: Any,
        assessment_type: str,
        answers: list[int],
        user_message: str,
        expected_language: str,
    ) -> ConversationResponse:
        pause_questionnaire_session(session, active_session)
        zh_emo = expected_language == "zh"
        crisis_hit = any(kw in user_message for kw in CRISIS_KEYWORDS)
        if crisis_hit:
            emo_reply = build_crisis_reply(
                RiskResult(risk_level="high", risk_types=[], needs_crisis_mode=True, reason="危机信号出现在问卷进行中"),
                user_message=user_message,
                expected_language=expected_language,
            )
            risk_level_emo, risk_reason_emo = "high", "Crisis keywords in mid-questionnaire message."
        else:
            emo_reply = (
                f"好，{guide.title}先放一放，已答的 {len(answers)} 题都保存了，"
                f"之后想继续时说一声「继续{guide.title}」就行。"
                "你刚才说的话我听到了——想聊聊现在的感受吗？"
                if zh_emo
                else (
                    f"Let's set the {guide.title} aside for now — your {len(answers)} answers are saved; "
                    "just ask to continue whenever you like. "
                    "I heard what you just said — would you like to talk about how you're feeling?"
                )
            )
            risk_level_emo, risk_reason_emo = "low", "Emotional disclosure during questionnaire; questionnaire paused."
        return self._build_response(
            session_id=active_session.id,
            mode="crisis" if crisis_hit else "support",
            reply_text=emo_reply,
            summary=f"Questionnaire {assessment_type} paused after emotional disclosure.",
            risk_level=risk_level_emo,
            risk_reason=risk_reason_emo,
            debug={
                "source": "questionnaire_emotional_pause",
                "llm_used": False,
                "fallback_used": False,
                "assessment_type": assessment_type,
            },
        )

    def _handle_valid_answer(
        self,
        payload: ConversationRequest,
        session: Session,
        active_session: Any,
        guide: Any,
        assessment_type: str,
        answer_value: int,
        expected_language: str,
    ) -> ConversationResponse:
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
                    f"Questionnaire {assessment_type} progress {updated_view.current_index}/{updated_view.total_items}."
                ),
                question_options=_options_payload(updated_view.next_item.options),
                debug={
                    "source": "questionnaire_progress",
                    "llm_used": False,
                    "fallback_used": False,
                    "assessment_type": assessment_type,
                },
            )

        return self._complete_assessment(
            payload, session, updated, guide, assessment_type, updated_answers, expected_language
        )

    def _complete_assessment(
        self,
        payload: ConversationRequest,
        session: Session,
        updated: Any,
        guide: Any,
        assessment_type: str,
        updated_answers: list[int],
        expected_language: str,
    ) -> ConversationResponse:
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
            summary=(f"Completed questionnaire {assessment_type} with score {result.score} ({result.severity_band})."),
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

    # ------------------------------------------------------------------
    # 无进行中会话：恢复 / 冷却 / 新开
    # ------------------------------------------------------------------

    def _handle_new_request(
        self,
        payload: ConversationRequest,
        session: Session,
        requested: str,
    ) -> ConversationResponse | None:
        expected_language = _detect_expected_language(payload.message)
        guide = questionnaire_guide(requested, language=expected_language)
        zh = expected_language == "zh"

        paused = get_paused_questionnaire_session(session, payload.user_id, requested)
        if paused is not None:
            return self._resume_paused(payload, session, paused, guide, requested, expected_language)

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

    def _resume_paused(
        self,
        payload: ConversationRequest,
        session: Session,
        paused: Any,
        guide: Any,
        requested: str,
        expected_language: str,
    ) -> ConversationResponse:
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
