from psych_support_bot.ai.schemas.messages import ConversationMode, RiskLevel
from psych_support_bot.ai.utils.text_matching import _contains_keyword, _normalize_text

OPEN_EXPLORATION_KEYWORDS = [
    "不知道",
    "说不清",
    "乱",
    "confused",
    "not sure",
    "overwhelmed",
    "why",
]

STRONG_PATTERN_KEYWORDS = [
    "总是",
    "每次",
    "反复",
    "every time",
    "again and again",
]

WEAK_PATTERN_KEYWORDS = [
    "一直",
    "always",
    "keep",
]

CONTRADICTION_KEYWORDS = [
    "但是",
    "可是",
    "又",
    "明明",
    "but",
    "however",
    "yet",
    "although",
]

AVOIDANCE_KEYWORDS = [
    "随便",
    "无所谓",
    "不想说",
    "算了",
    "whatever",
    "doesn't matter",
    "don't want to say",
]

ABSOLUTIST_KEYWORDS = [
    "一定",
    "永远",
    "根本",
    "完全",
    "注定",
    "all",
    "always",
    "never",
    "completely",
    "totally",
    "nothing",
    "everything",
]

MINIMIZATION_KEYWORDS = [
    "其实没事",
    "也还好",
    "不严重",
    "小事",
    "it's fine",
    "not a big deal",
    "doesn't matter",
]

RELATIONAL_DISCLOSURE_KEYWORDS = [
    "关系",
    "伴侣",
    "男朋友",
    "女朋友",
    "他说",
    "她说",
    "问我",
    "开口",
    "表达",
    "说出来",
    "relationship",
    "partner",
    "he asked",
    "she asked",
    "open up",
]

EXHAUSTION_KEYWORDS = [
    "累",
    "疲惫",
    "没劲",
    "提不起劲",
    "不想动",
    "心慌",
    "绷着",
    "tired",
    "exhausted",
    "drained",
    "burned out",
]


def _matches_any(normalized: str, compact: str, keywords: list[str]) -> bool:
    return any(_contains_keyword(normalized, compact, keyword) for keyword in keywords)


def determine_interview_process(
    *,
    user_message: str,
    mode: ConversationMode,
    risk_level: RiskLevel,
) -> dict[str, str | bool]:
    normalized, compact = _normalize_text(user_message)
    has_open_exploration = _matches_any(normalized, compact, OPEN_EXPLORATION_KEYWORDS)
    has_strong_pattern = _matches_any(normalized, compact, STRONG_PATTERN_KEYWORDS)
    has_weak_pattern = _matches_any(normalized, compact, WEAK_PATTERN_KEYWORDS)
    has_contradiction = _matches_any(normalized, compact, CONTRADICTION_KEYWORDS)
    has_avoidance = _matches_any(normalized, compact, AVOIDANCE_KEYWORDS)
    has_absolutist = _matches_any(normalized, compact, ABSOLUTIST_KEYWORDS)
    has_minimization = _matches_any(normalized, compact, MINIMIZATION_KEYWORDS)
    has_relational_disclosure = _matches_any(normalized, compact, RELATIONAL_DISCLOSURE_KEYWORDS)
    has_exhaustion = _matches_any(normalized, compact, EXHAUSTION_KEYWORDS)
    has_pattern = has_strong_pattern or (
        has_weak_pattern and (has_contradiction or has_absolutist or has_relational_disclosure)
    )
    has_actionable_avoidance = has_avoidance and (has_relational_disclosure or not has_exhaustion)

    stage = "engagement"
    question_strategy = "open"
    loop_hint = "Start broad, reflect the main concern, then narrow only after the user gives specifics."
    challenge_allowed = False

    if mode == "crisis" or risk_level in {"high", "critical"}:
        return {
            "interview_stage": "safety_stabilization",
            "question_strategy": "directive",
            "challenge_allowed": False,
            "loop_hint": "Stabilize first. Ask only brief safety-focused questions if needed.",
        }

    if mode == "assessment":
        return {
            "interview_stage": "structured_assessment",
            "question_strategy": "clarifying",
            "challenge_allowed": False,
            "loop_hint": "Clarify symptoms, frequency, triggers, and impact before offering an interpretation.",
        }

    if has_open_exploration:
        stage = "exploration"
        question_strategy = "open"
        loop_hint = (
            "Use open questions to uncover the situation, then reflect back the user's own words before narrowing."
        )

    if has_pattern:
        stage = "pattern_analysis"
        question_strategy = "looping"
        loop_hint = (
            "Track sequence: trigger -> thought -> feeling -> action -> consequence, and revisit the unclear link."
        )

    if has_contradiction:
        stage = "hypothesis_testing"
        question_strategy = "clarifying"
        challenge_allowed = True
        loop_hint = "Surface the tension between two statements and test which one better matches lived experience."

    if has_absolutist:
        stage = "hypothesis_testing"
        question_strategy = "gentle_challenge"
        challenge_allowed = True
        loop_hint = (
            "Test absolute conclusions by asking for evidence, exceptions, and what would count as a different outcome."
        )

    if has_actionable_avoidance or has_minimization:
        stage = "resistance_exploration"
        question_strategy = "gentle_challenge"
        challenge_allowed = True
        loop_hint = "Gently question avoidance or minimization, name the hesitation, and invite one concrete example instead of moving away."

    if has_pattern and (has_contradiction or has_absolutist):
        stage = "hypothesis_testing"
        question_strategy = "looping"
        challenge_allowed = True
        loop_hint = "Map the repeated sequence first, then test the point where the user's explanation becomes contradictory or overly absolute."

    if has_exhaustion and not (has_contradiction or has_absolutist or has_actionable_avoidance):
        stage = "exploration"
        question_strategy = "open"
        challenge_allowed = False
        loop_hint = "Clarify whether the exhaustion is physical, emotional, relational, or anticipatory before challenging the user's explanation."

    if mode == "planning":
        stage = "planning"
        question_strategy = "clarifying"
        loop_hint = "Clarify constraints, supports, and one realistic next step before making a plan."
        challenge_allowed = challenge_allowed or True

    if mode == "intervention" and stage == "engagement":
        stage = "formulation"
        question_strategy = "clarifying"
        loop_hint = "Clarify what the user has already tried, what changed, and what keeps the problem going before teaching a technique."

    return {
        "interview_stage": stage,
        "question_strategy": question_strategy,
        "challenge_allowed": challenge_allowed,
        "loop_hint": loop_hint,
    }
