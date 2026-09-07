import logging
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
from psych_support_bot.domain.assessments.service import classify_disengage
from psych_support_bot.infra.db.exercise_repositories import save_exercise_record
from psych_support_bot.infra.db.repositories import (
    build_memory_snapshot,
    build_user_history_text,
    get_latest_assessment,
    get_recent_risk_level,
    get_session_messages,
    get_user_sessions,
    save_conversation_result,
)
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output
from psych_support_bot.services.questionnaire_flow import QuestionnaireFlow
from psych_support_bot.services.support import _days_since, _detect_expected_language

# How long a screening result with needs_safety_followup keeps enforcing the
# elevated-risk floor on every incoming message.
SAFETY_FLOOR_WINDOW_DAYS = 7

# 逐字近史条数：以标准 API 格式进 prompt 的最近 user/assistant 消息上限。
# 覆盖最近 3 个完整问答对——「换个方向吧」这类指代性消息的可解读窗口。
RECENT_HISTORY_TURNS = 6


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
        """问卷会话状态机（P3 拆分至 services/questionnaire_flow.py，
        行为逐字保留）：进行中作答/暂停/放弃/情绪倾诉/无效重推/完成结算，
        以及恢复/冷却拦截/新开。"""
        return QuestionnaireFlow(self._build_response).handle(payload, session)

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

        # 语言检测前移：记录层记忆模块需按语言口径渲染，必须先于
        # build_memory_snapshot 完成。
        prior_messages = get_session_messages(session, session_id) if payload.session_id else []
        expected_language = _detect_expected_language(payload.message, prior_messages)
        # 逐字近史（P3 上下文拼接修复）：最近 6 条 user/assistant 消息以标准
        # API 格式追加在消息末尾——「换个方向吧」这类上下文依赖型消息此前
        # 因模型看不到逐字近史而被误读（Langfuse 2026-09-06 实证：摘要过期
        # + 粘贴截断，模型从记忆碎片里抓线头）。
        recent_history = [
            {"role": str(msg.role), "content": str(msg.content)}
            for msg in prior_messages[-RECENT_HISTORY_TURNS:]
            if msg.role in {"user", "assistant"} and (msg.content or "").strip()
        ]

        memory_summary = payload.memory_summary or build_memory_snapshot(
            session, payload.user_id, language=expected_language
        )
        # 情绪扫描专用通道：用户原话 + 会话摘要，不含记录层渲染文本。
        user_history_text = payload.memory_summary or build_user_history_text(session, payload.user_id)
        # 结构化风险通道：近 7 天最近一次 high/critical RiskEvent（跨轮升级主来源）。
        recent_risk_level = get_recent_risk_level(session, payload.user_id)

        state: GraphState = {
            "user_id": payload.user_id,
            "session_id": session_id,
            "user_message": payload.message,
            "conversation_intent": "new_request",
            "memory_summary": memory_summary,
            "user_history_text": user_history_text,
            "recent_risk_level": recent_risk_level,
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
            # 逐字近史（标准 API 格式，追加在消息末尾）
            "recent_history": recent_history,
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
                    reply=GeneratedReply(text=reply_text, style="support"),
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
