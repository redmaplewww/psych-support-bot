from psych_support_bot.ai.knowledge.index import detect_topics
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.ai.tools.knowledge_base import get_knowledge_context
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output


def load_knowledge_context(state: GraphState) -> GraphState:
    with trace_span(
        "node.knowledge_loader",
        input={"user_message": state["user_message"], "mode": state["mode"]},
    ) as obs:
        # LLM 语义 topics（risk_classifier 阶段产出，闭集校验过）并进检索
        # 通道：词表外表达（"心情很低落"）由此可达。为空时与原行为一致。
        llm_topics = list(state.get("llm_topics") or [])
        topics = list(dict.fromkeys([*llm_topics, *detect_topics(state["user_message"])]))
        state["topics"] = topics
        state["knowledge_context"] = get_knowledge_context(
            mode=state["mode"],
            risk_level=state["risk_result"].risk_level,
            user_message=state["user_message"],
            extra_topics=llm_topics,
        )
        update_span_output(
            obs,
            {
                "topics": topics,
                "llm_topics": llm_topics,
                "knowledge_context_len": len(state["knowledge_context"]),
            },
        )
    return state
