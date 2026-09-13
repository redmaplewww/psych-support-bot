import json
import logging
import re
from collections.abc import Iterator
from typing import Any, cast
from uuid import uuid4

from sqlalchemy.orm import Session

from psych_support_bot.ai.graphs.conversation import conversation_graph
from psych_support_bot.ai.nodes.safety_reviewer import scan_sentence_speakable
from psych_support_bot.ai.practice_flow import (
    PRACTICE_OFFER_MARKER,
    PRACTICE_PAUSE_CHIP,
    PRACTICE_TAG,
)
from psych_support_bot.ai.profile.display_dict import friendly_label
from psych_support_bot.ai.profile.extractor import record_turn_interventions, run_turn_extraction
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
from psych_support_bot.domain.consents import DISCLAIMER_VERSION
from psych_support_bot.infra.db.exercise_repositories import save_exercise_record
from psych_support_bot.infra.db.practice_repositories import (
    complete_practice_session,
    create_practice_session,
    get_active_practice_session,
    get_paused_practice_session,
    pause_practice_session,
    record_practice_step,
    reset_practice_session,
)
from psych_support_bot.infra.db.profile_repositories import list_question_candidates
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

# 「先停一下」chip（练习进行中的每轮都带——退出练习与进入同样容易）。
PRACTICE_PAUSE_CHIP_OPTION = {
    "label": PRACTICE_PAUSE_CHIP,
    "send": PRACTICE_PAUSE_CHIP,
}


logger = logging.getLogger(__name__)

# LLM→TTS 句子级流式：从累积 token 缓冲切「已完整」句子。句末标点为界；
# 无句末标点但已攒够长度时按软标点兜底切，避免首句迟迟不出声。
_SENTENCE_END = "。！？；!?;\n"
_SENTENCE_SOFT = "，、：,: "
_FIRST_SENTENCE_MIN = 14  # 首句兜底切阈值（字）——首句越短，整句合成越早完成、首响越快
_LATER_SENTENCE_MIN = 56  # 后续句兜底切阈值——句更长→边界更少，句间空隙不增


def _split_complete_sentences(buffer: str, *, first: bool) -> tuple[list[str], str]:
    """返回 (完整句列表, 剩余未完成尾部)。调用方以 remainder 作为新缓冲续累。"""
    sentences: list[str] = []
    cur = ""
    soft_min = _FIRST_SENTENCE_MIN if first else _LATER_SENTENCE_MIN
    for ch in buffer:
        cur += ch
        stripped = cur.strip()
        if ch in _SENTENCE_END:
            if stripped:
                sentences.append(stripped)
            cur = ""
        elif ch in _SENTENCE_SOFT and len(stripped) >= soft_min:
            sentences.append(stripped)
            cur = ""
    return sentences, cur


def _normalize_for_compare(text: str) -> str:
    """审查前后文本比对归一化：去空白，避免仅空格差异误触发 revise。"""
    return re.sub(r"\s+", "", text or "")


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

    def _load_active_practice(self, session: Session, user_id: str) -> dict[str, Any] | None:
        """预注入图内练习状态（active 优先，其次 paused）。

        GraphState 每轮从 DB 重建，多轮练习的推进状态必须每轮重读
        （与问卷会话同一模式）；图内不持 DB 会话，落库在图结束后。
        """

        record = get_active_practice_session(session, user_id)
        if record is None:
            record = get_paused_practice_session(session, user_id, PRACTICE_TAG)
        if record is None:
            return None
        try:
            responses = json.loads(record.step_responses_json or "[]")
            transcript = json.loads(record.guidance_transcript_json or "[]")
        except (TypeError, ValueError):
            responses, transcript = [], []
        return {
            "id": record.id,
            "tag": record.exercise_tag,
            "step": record.current_step,
            "responses": responses,
            "transcript": transcript,
            "status": record.status,
        }

    def _apply_practice_transition(
        self,
        session: Session,
        result: GraphState,
        payload: ConversationRequest,
        response: ConversationResponse,
    ) -> None:
        """图结束后的练习状态落库（practice_responder 的裁决在此执行）。

        危机支配：本轮走危机路径时 practice_responder 未被调用，
        进行中的练习在此自动暂停。练习轮附「先停一下」chip——
        退出练习必须和进入一样容易。
        """
        from psych_support_bot.infra.db.models import PracticeSessionRecord

        action = str(result.get("practice_action") or "")
        practice = result.get("active_practice") or {}
        record_id = practice.get("id")
        reply_text = result["generated_reply"].text

        if result["mode"] == "crisis":
            if record_id and practice.get("status") == "active":
                record = session.get(PracticeSessionRecord, record_id)
                if record is not None and record.status == "active":
                    pause_practice_session(session, record)
            return

        if not action:
            return

        if action == "offer":
            response.question_options = [{"label": PRACTICE_OFFER_MARKER, "send": PRACTICE_OFFER_MARKER}]
        elif action == "start":
            create_practice_session(session, payload.user_id, PRACTICE_TAG, disclaimer_version=DISCLAIMER_VERSION)
            response.question_options = [dict(PRACTICE_PAUSE_CHIP_OPTION)]
        elif action == "advance":
            record = session.get(PracticeSessionRecord, record_id) if record_id else None
            if record is not None and record.status == "active":
                record_practice_step(session, record, user_reply=payload.message, guide_reply=reply_text)
            response.question_options = [dict(PRACTICE_PAUSE_CHIP_OPTION)]
        elif action == "complete":
            record = session.get(PracticeSessionRecord, record_id) if record_id else None
            if record is not None and record.status == "active":
                record_practice_step(session, record, user_reply=payload.message, guide_reply=reply_text)
                complete_practice_session(session, record)
                # 练习记录落库（ExerciseRecord）：guidance_transcript 首次由
                # 对话内完成路径填充；收尾语即 ai_feedback。
                save_exercise_record(
                    session,
                    payload.user_id,
                    PRACTICE_TAG,
                    source="chat",
                    step_responses=json.loads(record.step_responses_json or "[]"),
                    guidance_transcript=json.loads(record.guidance_transcript_json or "[]"),
                    ai_feedback=reply_text,
                )
        elif action == "pause":
            record = session.get(PracticeSessionRecord, record_id) if record_id else None
            if record is not None and record.status == "active":
                pause_practice_session(session, record)
        elif action == "resume":
            record = session.get(PracticeSessionRecord, record_id) if record_id else None
            if record is not None and record.status == "paused":
                record.status = "active"
                session.commit()
                session.refresh(record)
            response.question_options = [dict(PRACTICE_PAUSE_CHIP_OPTION)]
        elif action == "restart":
            record = session.get(PracticeSessionRecord, record_id) if record_id else None
            if record is not None:
                reset_practice_session(session, record)
            else:
                create_practice_session(session, payload.user_id, PRACTICE_TAG, disclaimer_version=DISCLAIMER_VERSION)
            response.question_options = [dict(PRACTICE_PAUSE_CHIP_OPTION)]

        # debug 契约（assessment_card 同款模式）：前端与巡检可辨识练习轮
        if action:
            response.debug["source"] = "practice_guide"
            response.debug["practice_action"] = action
            response.debug["practice_step"] = practice.get("step")

    def _build_state(self, payload: ConversationRequest, session: Session) -> tuple[GraphState, str, str]:
        """从 DB 重建本轮 GraphState（respond 与 respond_stream 共用）。

        返回 (state, session_id, expected_language)。
        """
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

        # 结构化风险通道：近 7 天最近一次 high/critical RiskEvent（跨轮升级主来源）。
        # 取序前移：画像层的动态预算与 D7 优先渲染需要它作为轮次上下文。
        recent_risk_level = get_recent_risk_level(session, payload.user_id)
        # K2 质询闭环：待验证画像假设（图内不持 DB 会话，图启动前载入）。
        profile_question_candidates = [
            label
            for belief in list_question_candidates(session, payload.user_id)
            if (label := friendly_label(belief.key)) is not None
        ]
        memory_summary = payload.memory_summary or build_memory_snapshot(
            session,
            payload.user_id,
            language=expected_language,
            user_message=payload.message,
            recent_risk_level=recent_risk_level,
        )
        # 情绪扫描专用通道：用户原话 + 会话摘要，不含记录层渲染文本。
        user_history_text = payload.memory_summary or build_user_history_text(session, payload.user_id)

        state: GraphState = {
            "user_id": payload.user_id,
            "session_id": session_id,
            "user_message": payload.message,
            "memory_summary": memory_summary,
            "user_history_text": user_history_text,
            "recent_risk_level": recent_risk_level,
            "profile_question_candidates": profile_question_candidates,
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
            # 图内引导练习：预注入进行中/暂停中的练习会话（无则 None）。
            "active_practice": self._load_active_practice(session, payload.user_id),
            # practice_responder 的路由输入（intent_router 裁决写入）与
            # 裁决输出（服务层落库读取），默认空。
            "practice_route": "",
            "practice_action": "",
            # LLM→TTS 句子级流式开关（默认关；respond_stream 置真）。
            "stream_tokens": False,
        }
        return state, session_id, expected_language

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

        state, session_id, expected_language = self._build_state(payload, session)
        with trace_span(
            "conversation_graph.invoke",
            input={
                "user_id": payload.user_id,
                "session_id": session_id,
                "message": payload.message,
                "mode": "support",
            },
            metadata={"memory_summary": state["memory_summary"]},
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
                # 图挂了意味着本轮练习裁决不可得：进行中的练习一并暂停，
                # 避免用户收到兜底回复后仍被下一轮的过期状态推着做步骤。
                if state["active_practice"] and state["active_practice"].get("status") == "active":
                    from psych_support_bot.infra.db.models import PracticeSessionRecord

                    practice_record = session.get(PracticeSessionRecord, state["active_practice"]["id"])
                    if practice_record is not None and practice_record.status == "active":
                        pause_practice_session(session, practice_record)
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
        return self._finalize(result, payload, session, session_id)

    def respond_stream(self, payload: ConversationRequest, session: Session) -> Iterator[dict[str, Any]]:
        """LLM→TTS 句子级流式：产出 {type: sentence|revise|final} 事件序列。

        仅常规 support 普通 LLM 路径流式（response_generator 经 get_stream_writer
        推 token）；问卷/危机/会诊等确定性或非流式路径直接单发 final。每句先过
        与全文审查同源的纯规则扫描决定是否朗读；图跑完 _finalize 后，若最终审查
        文本与已朗读内容实质不同则发 revise（前端停读+替换）。任何流式异常回退到
        非流式 respond（此时尚未持久化，无重复副作用）。
        """
        # 问卷流是确定性的（分页卡片），不流式：整段处理并单发 final
        questionnaire_response = self._handle_questionnaire_flow(payload, session)
        if questionnaire_response is not None:
            save_conversation_result(
                session=session,
                response=questionnaire_response,
                user_message=payload.message,
                user_id=payload.user_id,
            )
            yield {"type": "final", "response": questionnaire_response}
            return

        state, session_id, expected_language = self._build_state(payload, session)
        state["stream_tokens"] = True
        # 逐句扫描保守取 challenge_allowed=False：质问式句子未确认放行前不抢跑朗读
        pending = ""
        spoken: list[str] = []
        final_state: GraphState | None = None
        try:
            for mode, chunk in conversation_graph.stream(cast(Any, state), stream_mode=["custom", "values"]):
                if mode == "custom":
                    text = chunk.get("text", "") if isinstance(chunk, dict) else ""
                    if not text:
                        continue
                    pending += text
                    sentences, pending = _split_complete_sentences(pending, first=not spoken)
                    for sent in sentences:
                        spoken.append(sent)
                        if scan_sentence_speakable(sent, challenge_allowed=False, expected_language=expected_language):
                            yield {"type": "sentence", "text": sent}
                elif mode == "values":
                    final_state = cast(GraphState, chunk)
        except Exception:
            logger.exception("respond_stream graph failed; falling back to non-streaming respond.")
            yield {"type": "final", "response": self.respond(payload, session)}
            return

        if final_state is None:
            yield {"type": "final", "response": self.respond(payload, session)}
            return

        # 收尾残留（无句末标点结束的尾句）
        tail = pending.strip()
        if tail:
            spoken.append(tail)
            if scan_sentence_speakable(tail, challenge_allowed=False, expected_language=expected_language):
                yield {"type": "sentence", "text": tail}

        response = self._finalize(final_state, payload, session, session_id)

        # 全文兜底审查差异 → revise（前端停读并用审查后文本替换气泡）
        reviewed = (response.reply.text or "").strip()
        if reviewed and _normalize_for_compare(reviewed) != _normalize_for_compare("".join(spoken)):
            yield {"type": "revise", "text": reviewed}

        yield {"type": "final", "response": response}

    def _finalize(
        self,
        result: GraphState,
        payload: ConversationRequest,
        session: Session,
        session_id: str,
    ) -> ConversationResponse:
        """图跑完后的统一收尾：构建响应 + 练习状态迁移 + 消息/练习持久化。

        respond 与 respond_stream 共用，保证两条路径落库与响应结构逐字一致。
        """
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
        # 图内练习状态落库（advance/pause/complete/start/resume/restart）+
        # crisis 自动暂停 + 练习 chips/进度 debug。在 save_conversation_result
        # 之前执行，让练习状态迁移与本轮消息同批持久化。
        self._apply_practice_transition(session, result, payload, response)
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
        # K1b 画像提取（fail-open）：D1 复用图内 topics，D4 吃练习完成信号；
        # 危机轮零提取；全量统计落 profile_extraction_stats。异常只记日志，
        # 绝不影响本轮响应（与记忆模块 fail-open 同约定）。
        try:
            run_turn_extraction(
                session,
                user_id=payload.user_id,
                session_id=session_id,
                topics=list(result.get("topics") or []),
                risk_level=str(result["risk_result"].risk_level),
                exercise_tag=completed_tag
                or (PRACTICE_TAG if str(result.get("practice_action") or "") == "complete" else None),
                valence_text=payload.message,
                turn_count=int(result.get("turn_count") or 0),
            )
        except Exception:  # pragma: no cover - run_turn_extraction 内部已兜底
            logger.exception("Profile extraction hook raised; conversation response unaffected.")
        # K2c 干预→反应事件（动作元数据 only，fail-open）。
        try:
            record_turn_interventions(
                session,
                user_id=payload.user_id,
                session_id=session_id,
                practice_action=str(result.get("practice_action") or ""),
                exercise_tag=completed_tag
                or (PRACTICE_TAG if str(result.get("practice_action") or "") == "complete" else None),
                question_candidates=list(result.get("profile_question_candidates") or []),
                no_question_mode=bool(result.get("no_question_mode")),
                mode=str(result["mode"]),
                risk_level=str(result["risk_result"].risk_level),
            )
        except Exception:  # pragma: no cover - record_turn_interventions 内部已兜底
            logger.exception("Intervention event hook raised; conversation response unaffected.")
        return response


conversation_service = ConversationService()
