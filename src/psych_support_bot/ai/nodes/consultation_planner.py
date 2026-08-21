import logging

from psych_support_bot.ai.consultation import (
    consultation_agent_labels,
    should_trigger_multidisciplinary_consultation,
)
from psych_support_bot.ai.interview import determine_interview_process
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.ai.utils.text_matching import (
    _contains_keyword,
    _normalize_text,
)

logger = logging.getLogger(__name__)

# Emotion direction indicators for cross-turn contradiction detection.
# When the previous turn expresses negative state and the current turn
# expresses its opposite (or vice versa), the loop_hint should signal
# the contradiction so the LLM can gently surface the tension.
NEGATIVE_STATE_KEYWORDS = [
    "焦虑",
    "紧张",
    "害怕",
    "恐惧",
    "担心",
    "抑郁",
    "低落",
    "沮丧",
    "绝望",
    "悲伤",
    "难过",
    "痛苦",
    "累",
    "疲惫",
    "无力",
    "没劲",
    "失眠",
    "睡不着",
    "睡不好",
    "不想活",
    "没意义",
    "活着没意思",
    "anxious",
    "anxiety",
    "depressed",
    "depression",
    "hopeless",
    "exhausted",
    "tired",
    "insomnia",
    "can't sleep",
    "sad",
    "miserable",
    "struggling",
    "overwhelmed",
]

POSITIVE_STATE_KEYWORDS = [
    "好了",
    "没事了",
    "不焦虑了",
    "不紧张了",
    "不害怕了",
    "开心",
    "轻松",
    "放松",
    "平静",
    "好多了",
    "有希望",
    "有动力",
    "想开了",
    "放下了",
    "睡得着",
    "睡得好",
    "不失眠了",
    "better",
    "fine",
    "okay",
    "good",
    "relieved",
    "calm",
    "relaxed",
    "hopeful",
    "motivated",
    "no longer",
    "recovered",
    "over it",
]

# Negation prefixes: when a negative-state keyword is preceded by one of these
# within a small window, the keyword is considered negated (i.e. the user is
# expressing the *absence* of the negative state, which is a positive signal).
_NEGATION_PREFIXES = ("不", "没", "不再", "no longer", "not ", "never", "without")
_NEGATION_WINDOW = 6


def _keyword_positions(text: str, keyword: str) -> list[int]:
    """Find all positions of keyword in text (case-insensitive substring search)."""
    positions: list[int] = []
    lower_text = text.lower()
    lower_kw = keyword.lower()
    start = 0
    while True:
        idx = lower_text.find(lower_kw, start)
        if idx < 0:
            break
        positions.append(idx)
        start = idx + len(lower_kw)
    return positions


def _is_negated(text: str, keyword: str, positions: list[int]) -> bool:
    """Check if any occurrence of keyword is negated by a nearby prefix.

    Returns True if ALL occurrences are negated, False if at least one is not.
    """
    if not positions:
        return False
    lower_text = text.lower()
    for pos in positions:
        prefix_start = max(0, pos - _NEGATION_WINDOW)
        prefix = lower_text[prefix_start:pos]
        if not any(neg in prefix for neg in _NEGATION_PREFIXES):
            return False  # at least one non-negated occurrence
    return True


def _extract_emotion_directions(text: str) -> set[str]:
    """Return a set of emotion directions found in text: {'negative', 'positive'}.

    Uses the same _normalize_text + _contains_keyword toolchain as the rest
    of the codebase for consistency. Negative keywords that are negated by
    a nearby prefix (e.g. "不焦虑") are not counted as negative.
    """
    normalized, compact = _normalize_text(text)
    directions: set[str] = set()

    for kw in NEGATIVE_STATE_KEYWORDS:
        if _contains_keyword(normalized, compact, kw):
            # For Chinese keywords, check negation via prefix proximity;
            # for English keywords, _contains_keyword already uses word
            # boundary matching so negation is less of a concern, but we
            # still check for robustness.
            kw_norm, _ = _normalize_text(kw)
            positions = _keyword_positions(normalized, kw_norm)
            if not _is_negated(normalized, kw_norm, positions):
                directions.add("negative")
                break

    for kw in POSITIVE_STATE_KEYWORDS:
        if _contains_keyword(normalized, compact, kw):
            directions.add("positive")
            break

    return directions


def _detect_cross_turn_contradiction(memory_summary: str, current_message: str) -> str | None:
    """Compare emotion directions between memory and current message.

    Returns a contradiction hint string if a clear directional shift is found,
    or None if no contradiction is detected.

    The memory_summary is a free-form string assembled by build_memory_snapshot
    (profile || summary || assessment || checkin || recent_messages).
    Rather than trying to parse its internal structure (which is fragile),
    we run emotion direction extraction on the entire string. This is safe
    because the memory snapshot only contains the user's own words and
    metadata—any negative/positive signal in it reflects the user's prior state.
    """
    if not memory_summary or not current_message:
        return None

    prev_directions = _extract_emotion_directions(memory_summary)
    curr_directions = _extract_emotion_directions(current_message)

    # Contradiction: previous turn was negative, current turn is positive (or vice versa)
    if prev_directions and curr_directions and prev_directions != curr_directions:
        if prev_directions == {"negative"} and curr_directions == {"positive"}:
            return (
                "Cross-turn contradiction: user previously expressed distress but now "
                "reports improvement. Gently acknowledge the shift and explore what changed "
                "before accepting the positive report at face value."
            )
        if prev_directions == {"positive"} and curr_directions == {"negative"}:
            return (
                "Cross-turn contradiction: user previously reported doing better but now "
                "expresses distress. Acknowledge the setback, validate that recovery is "
                "non-linear, and explore what triggered the change."
            )

    return None


def plan_consultation(state: GraphState) -> GraphState:
    required = should_trigger_multidisciplinary_consultation(
        user_message=state["user_message"],
        mode=state["mode"],
        risk_level=state["risk_result"].risk_level,
    )
    state["consultation_required"] = required
    state["consultation_agents"] = consultation_agent_labels() if required else []
    state["consultation_notes"] = ""
    state["consultation_opinions"] = []
    interview_process = determine_interview_process(
        user_message=state["user_message"],
        mode=state["mode"],
        risk_level=state["risk_result"].risk_level,
    )
    state["interview_stage"] = str(interview_process["interview_stage"])
    state["question_strategy"] = str(interview_process["question_strategy"])
    state["challenge_allowed"] = bool(interview_process["challenge_allowed"])

    loop_hint = str(interview_process["loop_hint"])

    # B2.1: Cross-turn contradiction detection
    # If the user's emotional direction has shifted between turns,
    # prepend a contradiction hint to the loop_hint so the LLM can
    # gently surface the tension rather than ignoring the shift.
    contradiction_hint = _detect_cross_turn_contradiction(state.get("memory_summary", ""), state["user_message"])
    if contradiction_hint:
        loop_hint = contradiction_hint + " " + loop_hint
        logger.info("Cross-turn contradiction detected; loop_hint updated.")

    state["loop_hint"] = loop_hint
    return state
