from psych_support_bot.ai.schemas.messages import ConversationMode, RiskLevel
from psych_support_bot.ai.utils.text_matching import _contains_keyword, _normalize_text

CONSULTATION_AGENTS: tuple[dict[str, str], ...] = (
    {
        "key": "cbt",
        "label": "CBT Agent",
        "school": "Cognitive Behavioral Therapy",
        "focus": "automatic thoughts, behavior loops, maintaining factors, small experiments",
    },
    {
        "key": "psychodynamic",
        "label": "Psychodynamic Agent",
        "school": "Psychodynamic",
        "focus": "core conflicts, attachment themes, repeated relational patterns, emotional meaning",
    },
    {
        "key": "humanistic",
        "label": "Humanistic Agent",
        "school": "Person-Centered / Humanistic",
        "focus": "felt experience, unmet needs, self-worth, empathic understanding",
    },
    {
        "key": "act",
        "label": "ACT Agent",
        "school": "Acceptance and Commitment Therapy",
        "focus": "acceptance, defusion, values, psychological flexibility",
    },
    {
        "key": "dbt",
        "label": "DBT Agent",
        "school": "Dialectical Behavior Therapy",
        "focus": "emotion regulation, distress tolerance, interpersonal effectiveness, safety",
    },
)


CONSULT_TRIGGER_KEYWORDS = [
    "diagnosis",
    "diagnose",
    "treatment",
    "therapy",
    "therapist",
    "clinical",
    "case formulation",
    "诊断",
    "治疗",
    "疗法",
    "治疗方案",
    "干预",
    "会诊",
    "流派",
    "咨询师",
]


def consultation_agents() -> tuple[dict[str, str], ...]:
    return CONSULTATION_AGENTS


def consultation_agent_labels() -> list[str]:
    return [agent["label"] for agent in CONSULTATION_AGENTS]


def consultation_agent_descriptions() -> str:
    return "\n".join(f"- {agent['label']} ({agent['school']}): {agent['focus']}." for agent in CONSULTATION_AGENTS)


def should_trigger_multidisciplinary_consultation(
    *,
    user_message: str,
    mode: ConversationMode,
    risk_level: RiskLevel,
) -> bool:
    if risk_level in {"high", "critical"}:
        return False
    if mode in {"assessment", "intervention"}:
        return True

    normalized, compact = _normalize_text(user_message)
    return any(_contains_keyword(normalized, compact, keyword) for keyword in CONSULT_TRIGGER_KEYWORDS)
