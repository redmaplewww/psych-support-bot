from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output


def load_memory_context(state: GraphState) -> GraphState:
    with trace_span(
        "node.memory_loader",
        input={"session_summary": state.get("session_summary", "")},
    ) as obs:
        current = state.get("memory_summary") or "No long-term memory loaded yet."
        session = state.get("session_summary")

        if session:
            if current and current != "No long-term memory loaded yet.":
                state["memory_summary"] = f"{current} | Previous turn: {session}"
            else:
                state["memory_summary"] = session
        else:
            state["memory_summary"] = current

        update_span_output(obs, {"memory_summary": state["memory_summary"][:200]})
    return state
