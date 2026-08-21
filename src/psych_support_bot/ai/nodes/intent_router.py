from psych_support_bot.ai.routers.intent import REFUSAL_KEYWORDS, detect_mode
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.ai.utils.text_matching import _contains_keyword, _normalize_text


def route_intent(state: GraphState) -> GraphState:
    if state.get("mode") == "crisis":
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
    return state
