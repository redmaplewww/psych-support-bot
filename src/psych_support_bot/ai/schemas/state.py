from typing import TypedDict

from psych_support_bot.ai.schemas.messages import (
    ConversationMode,
    GeneratedReply,
    RiskResult,
)


class GraphState(TypedDict):
    user_id: str
    session_id: str
    user_message: str
    memory_summary: str
    knowledge_context: str
    mode: ConversationMode
    risk_result: RiskResult
    generated_reply: GeneratedReply
    session_summary: str
    topics: list[str]
    fallback_used: bool
    consultation_required: bool
    consultation_agents: list[str]
    consultation_notes: str
    consultation_opinions: list[dict[str, str]]
    interview_stage: str
    question_strategy: str
    challenge_allowed: bool
    loop_hint: str
    # B3.1: Exercise history and refusal history for personalized recommendations
    exercise_history: list[str]
    refusal_history: list[str]
    # Language determined from conversation history to keep language consistent
    # even when the current message is language-neutral (e.g. pure numbers).
    expected_language: str
    # Number of prior messages in this session; drives stage-floor escalation.
    turn_count: int
    # Minimum risk level enforced by the classifier ("elevated"/"high"/"" );
    # derived from recent screening results that flagged safety follow-up.
    safety_floor_risk_level: str
    # Disengagement preference: user asked NOT to be questioned this turn
    # ("我只想安静待一会儿"). Safe paths (crisis/high risk) ignore this.
    no_question_mode: bool
    # Most recent bot reply in this session ("" when none). The response
    # generator uses it to avoid serving a verbatim-identical reply twice.
    last_bot_reply: str
    # M2 首答延迟优化：risk_classifier 在规则判 low/elevated 且 support 模式时
    # 与风险 LLM 分类并行投机生成的回复（带 elevated 加温备注）。最终风险
    # 裁决仍 ≤ elevated 时 response_generator 直接采用，跳过自己的 LLM 调用；
    # 升级 high/critical 则丢弃走危机路径。投机失败为 None。
    speculative_reply: str | None
