from psych_support_bot.ai.safety.rules import classify_message_risk
from psych_support_bot.ai.schemas.messages import RiskResult
from psych_support_bot.ai.schemas.state import GraphState


def classify_risk(state: GraphState) -> GraphState:
    risk_result = classify_message_risk(state["user_message"])
    state["risk_result"] = RiskResult(**risk_result.model_dump())
    if state["risk_result"].needs_crisis_mode:
        state["mode"] = "crisis"
    return state
