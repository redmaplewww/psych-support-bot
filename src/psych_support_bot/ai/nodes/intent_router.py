from psych_support_bot.ai.routers.intent import REFUSAL_KEYWORDS, detect_mode
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.ai.utils.text_matching import _contains_keyword, _normalize_text
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output


def route_intent(state: GraphState) -> GraphState:
    with trace_span(
        "node.intent_router",
        input={"user_message": state["user_message"], "current_mode": state.get("mode", "")},
    ) as obs:
        if state.get("mode") == "crisis":
            update_span_output(obs, {"mode": "crisis", "skipped": True})
            return state

        # B3.2: Before re-routing, check if user is refusing an exercise.
        # If so, record the refused topic in refusal_history.
        normalized, compact = _normalize_text(state["user_message"])
        has_refusal = any(_contains_keyword(normalized, compact, kw) for kw in REFUSAL_KEYWORDS)
        if has_refusal and state.get("mode") == "intervention":
            refusal_history = state.get("refusal_history", [])
            # Record the topics that were active when the refusal occurred
            current_topics = state.get("topics", [])
            for topic in current_topics:
                if topic not in refusal_history:
                    refusal_history.append(topic)
            state["refusal_history"] = refusal_history

        state["mode"] = detect_mode(state["user_message"])
        update_span_output(obs, {
            "mode": state["mode"],
            "refusal_detected": has_refusal,
            "refusal_history": state.get("refusal_history", []),
        })
    return state
