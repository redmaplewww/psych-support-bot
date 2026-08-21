import logging

from psych_support_bot.ai.consultation import consultation_agent_descriptions
from psych_support_bot.ai.prompts.templates import build_crisis_safety_prompt
from psych_support_bot.ai.safety.crisis import build_crisis_reply
from psych_support_bot.ai.schemas.messages import GeneratedReply
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.infra.llm.generation import (
    generate_clinically_bounded_reply,
    generate_multidisciplinary_consultation,
)

logger = logging.getLogger(__name__)


def _inject_refusal_context(state: GraphState) -> None:
    """B3.3: If user has refused exercises before, inject context into loop_hint.

    This tells the LLM not to repeat recommendations for topics the user
    has already declined, improving personalization.
    """
    refusal_history = state.get("refusal_history", [])
    if not refusal_history:
        return
    refused_topics = ", ".join(refusal_history)
    existing_hint = state.get("loop_hint", "")
    refusal_note = (
        f"User has previously declined exercises related to: {refused_topics}. "
        "Do not recommend the same types of exercises again. "
        "Offer a different approach or explore why the previous suggestion did not fit."
    )
    state["loop_hint"] = refusal_note + " " + existing_hint if existing_hint else refusal_note


def generate_response(state: GraphState) -> GraphState:
    state["fallback_used"] = False
    risk_level = state["risk_result"].risk_level

    # B3.3: Inject refusal history into loop_hint before LLM generation
    _inject_refusal_context(state)

    # Only critical risk uses pure template reply (imminent danger)
    # High risk now goes through LLM with crisis safety prompt injected
    if risk_level == "critical":
        reply_text = build_crisis_reply(state["risk_result"], user_message=state["user_message"])
        state["consultation_opinions"] = []
    elif state["mode"] == "crisis" and risk_level == "high":
        # High-risk crisis: use LLM with crisis safety guidance
        try:
            reply_text = generate_clinically_bounded_reply(
                user_message=state["user_message"],
                mode="crisis",
                risk_level="high",
                memory_summary=state.get("memory_summary", ""),
                knowledge_context=build_crisis_safety_prompt(),
                consultation_required=False,
                consultation_agents=[],
                consultation_framework="",
                interview_stage="engagement",
                question_strategy="open",
                challenge_allowed=False,
                loop_hint="Prioritize safety, validation, and gentle redirection to support resources.",
            )
            state["consultation_opinions"] = []
        except Exception:
            logger.exception("LLM generation failed for high-risk; using crisis template fallback.")
            state["fallback_used"] = True
            reply_text = build_crisis_reply(state["risk_result"], user_message=state["user_message"])
    else:
        try:
            if state.get("consultation_required", False):
                reply_text, opinions = generate_multidisciplinary_consultation(
                    user_message=state["user_message"],
                    mode=state["mode"],
                    risk_level=state["risk_result"].risk_level,
                    memory_summary=state.get("memory_summary", ""),
                    knowledge_context=state.get("knowledge_context", ""),
                    consultation_framework=consultation_agent_descriptions(),
                    interview_stage=state.get("interview_stage", "engagement"),
                    question_strategy=state.get("question_strategy", "open"),
                    challenge_allowed=bool(state.get("challenge_allowed", False)),
                    loop_hint=state.get("loop_hint", "Start broad, reflect, then narrow."),
                )
                state["consultation_opinions"] = opinions
            else:
                reply_text = generate_clinically_bounded_reply(
                    user_message=state["user_message"],
                    mode=state["mode"],
                    risk_level=state["risk_result"].risk_level,
                    memory_summary=state.get("memory_summary", ""),
                    knowledge_context=state.get("knowledge_context", ""),
                    consultation_required=False,
                    consultation_agents=[],
                    consultation_framework="",
                    interview_stage=state.get("interview_stage", "engagement"),
                    question_strategy=state.get("question_strategy", "open"),
                    challenge_allowed=bool(state.get("challenge_allowed", False)),
                    loop_hint=state.get("loop_hint", "Start broad, reflect, then narrow."),
                )
                state["consultation_opinions"] = []
        except Exception:
            logger.exception("LLM generation failed; using template fallback.")
            state["fallback_used"] = True
            is_zh = any("\u4e00" <= char <= "\u9fff" for char in state.get("user_message", ""))
            if is_zh:
                reply_text = (
                    "我在这里陪你。虽然我现在遇到了一些技术困难，"
                    "但我仍然想支持你。我们可以先慢下来，聊一聊你现在的感受。"
                )
            else:
                reply_text = (
                    "I am here with you. Although I am experiencing some technical difficulty, "
                    "I still want to support you. Let us slow down and talk about how you are feeling right now."
                )

    state["generated_reply"] = GeneratedReply(
        text=reply_text,
        style=state["mode"],
        includes_action_step=True,
    )
    state["consultation_notes"] = (
        f"{len(state.get('consultation_opinions', []))} agents consulted; stage={state.get('interview_stage', 'engagement')}; question={state.get('question_strategy', 'open')}"
        if state.get("consultation_required")
        else f"single response path; stage={state.get('interview_stage', 'engagement')}; question={state.get('question_strategy', 'open')}"
    )
    return state
