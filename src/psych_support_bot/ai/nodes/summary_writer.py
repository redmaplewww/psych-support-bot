from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output


def write_summary(state: GraphState) -> GraphState:
    with trace_span(
        "node.summary_writer",
        input={"mode": state["mode"], "risk": state["risk_result"].risk_level},
    ) as obs:
        mode = state["mode"]
        risk = state["risk_result"].risk_level
        # 摘要只承载元数据与用户消息，不再内嵌 bot 回复原文（复读事故根因，
        # Langfuse 2026-09-02 c4fd09cc：summary 与 recent_messages 双通道把
        # 上一轮回复成品带进 prompt，模型照抄记忆区范例逐字复读）。回复全文
        # 已由 Message 表持久化，build_memory_snapshot 的 recent_excerpt 才是
        # 唯一进入上下文的原文通道；保留回复长度以便追踪长答/短答模式。
        topics = state.get("topics")
        user_msg = state["user_message"]
        consultation_required = state.get("consultation_required", False)
        consultation_agents = state.get("consultation_agents", [])
        consultation_opinions = state.get("consultation_opinions", [])
        interview_stage = state.get("interview_stage", "engagement")
        question_strategy = state.get("question_strategy", "open")
        consultation_suffix = ""
        if consultation_required:
            consultation_suffix = f", consultation={consultation_agents}, opinions={len(consultation_opinions)}"
        process_suffix = f", stage={interview_stage}, question={question_strategy}"

        if topics:
            summary = (
                f"User (mode={mode}, risk={risk}, topics={topics}{consultation_suffix}{process_suffix}): {user_msg[:100]}. "
                f"Bot replied ({len(state['generated_reply'].text)} chars)."
            )
        else:
            summary = (
                f"User (mode={mode}, risk={risk}{consultation_suffix}{process_suffix}): {user_msg[:100]}. "
                f"Bot replied ({len(state['generated_reply'].text)} chars)."
            )

        # Layer 3 of disengagement handling: persist the quiet preference into
        # the rolling session summaries so later turns inherit it via memory.
        if state.get("no_question_mode"):
            summary += " [user prefers NO questions right now — keep responses minimal until they re-engage]"

        # Persist taught self-help steps (e.g. breathing exercise) into the
        # rolling summary so later turns don't ask the user to start over.
        # 这 80 字是唯一保留的回复片段：教学步骤的"已教"凭证，防重复推荐。
        if mode == "intervention":
            taught_step = state["generated_reply"].text[:80]
            summary += f" [taught self-help step: {taught_step}]"

        state["session_summary"] = summary
        update_span_output(obs, {"session_summary": summary[:200]})
    return state
