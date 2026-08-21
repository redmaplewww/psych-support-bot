import logging
import re

from psych_support_bot.ai.safety.rules import classify_message_risk
from psych_support_bot.ai.schemas.messages import RiskResult
from psych_support_bot.ai.schemas.state import GraphState

logger = logging.getLogger(__name__)

# Patterns to detect previous elevated risk in memory_summary.
# Memory snapshot contains recent messages and risk info; we look for
# signs that the previous turn was classified as elevated distress.
_PREV_ELEVATED_PATTERNS = [
    # Risk level markers in session summary format
    r"risk\s*=\s*elevated",
    r"risk_level.*elevated",
    # Elevated distress keywords appearing in recent message excerpts
    # (these overlap with ELEVATED_RISK_KEYWORDS from rules.py)
    r"绝望|没有希望|没意义|撑不住|扛不住|快崩溃|睡不着|失眠|惊恐发作|喘不过气",
    r"hopeless|panic attack|worthless|not sleeping|better off dead",
]

_PREV_ELEVATED_REGEX = [re.compile(p, re.IGNORECASE) for p in _PREV_ELEVATED_PATTERNS]


def _has_previous_elevated(memory_summary: str) -> bool:
    """Check if memory_summary indicates the previous turn was elevated risk."""
    if not memory_summary:
        return False
    return any(pattern.search(memory_summary) for pattern in _PREV_ELEVATED_REGEX)


def classify_risk(state: GraphState) -> GraphState:
    risk_result = classify_message_risk(state["user_message"])
    state["risk_result"] = RiskResult(**risk_result.model_dump())

    # B2.2: Cross-turn risk tracking
    # If the current turn is elevated AND the previous turn was also elevated,
    # automatically upgrade to high risk. Persistent elevated distress across
    # consecutive turns signals accumulating risk that warrants closer attention.
    if risk_result.risk_level == "elevated":
        memory_summary = state.get("memory_summary", "")
        if _has_previous_elevated(memory_summary):
            state["risk_result"] = RiskResult(
                risk_level="high",
                risk_types=[*risk_result.risk_types, "cumulative_elevated"],
                needs_crisis_mode=True,
                reason=(
                    "Consecutive elevated distress across turns; "
                    "upgraded to high risk for safety. Original: " + risk_result.reason
                ),
            )
            logger.info("Cross-turn risk upgrade: elevated -> high (consecutive elevated detected)")

    if state["risk_result"].needs_crisis_mode:
        state["mode"] = "crisis"
    return state
