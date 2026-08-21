from psych_support_bot.ai.knowledge.index import detect_topics
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.ai.tools.knowledge_base import get_knowledge_context


def load_knowledge_context(state: GraphState) -> GraphState:
    topics = detect_topics(state["user_message"])
    state["topics"] = topics
    state["knowledge_context"] = get_knowledge_context(
        mode=state["mode"],
        risk_level=state["risk_result"].risk_level,
        user_message=state["user_message"],
    )
    return state
