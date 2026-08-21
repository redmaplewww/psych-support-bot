import logging
import re

from psych_support_bot.ai.prompts.templates import (
    build_boundary_prompt,
    build_context_prompt,
    build_output_prompt,
    build_role_prompt,
    build_system_guidance,
)
from psych_support_bot.ai.schemas.state import GraphState

logger = logging.getLogger(__name__)


def _build_leak_markers() -> tuple[str, ...]:
    role_text = build_role_prompt()
    boundary_text = build_boundary_prompt(risk_level="low")
    context_text = build_context_prompt(memory_summary="", knowledge_context="")
    output_text = build_output_prompt(mode="support", risk_level="low", user_message="hello")
    system_guidance_text = build_system_guidance(mode="support", risk_level="low")

    markers: list[str] = []

    if "You are a safety-first" in role_text:
        markers.append("You are a safety-first")
    if "You are a safety-first" in role_text:
        markers.append("You are a safety-first AI")
    for marker in [
        "Conversation mode:",
        "Current assessed risk level:",
        "Known user memory summary:",
        "Relevant practice context:",
    ]:
        if marker in boundary_text or marker in context_text or output_text:
            markers.append(marker)
    if "Respond with" in system_guidance_text or "Respond with" in output_text:
        markers.append("Respond with")

    extra_markers = [
        "System prompt",
        "You are a safety-first",
        "You are a safety-first AI psychological support assistant",
        "Conversation mode:",
        "Current assessed risk",
        "Current assessed risk level",
        "Known user memory summary:",
        "Relevant practice context:",
        "Relevant practice context",
        "Respond with",
        "系统提示",
        "你是一个安全优先的",
        "对话模式：",
        "当前评估风险",
        "已知用户记忆摘要：",
        "相关实践背景：",
        "请以",
    ]
    for m in extra_markers:
        if m not in markers:
            markers.append(m)

    return tuple(markers)


LEAK_MARKERS: tuple[str, ...] = _build_leak_markers()

# Diagnosis language patterns: LLM outputs that imply or state a diagnosis
DIAGNOSIS_PATTERNS: list[str] = [
    # English
    r"you (have|are suffering from|are experiencing) .{0,30}(depression|anxiety|ADHD|bipolar|OCD|PTSD|schizophrenia|personality disorder|panic disorder|social anxiety|GAD|MDD)",
    r"you (are|seem) (clinically )?(depressed|anxious|bipolar|autistic|schizophrenic)",
    r"your (diagnosis|condition|disorder) is",
    r"you (meet|meet the criteria for|qualify for) .{0,30}(diagnosis|disorder|condition)",
    r"based on (my|the) (assessment|evaluation|analysis),? you (have|are)",
    r"I (diagnose|conclude|determine) that you",
    r"you (need|should) (see|consult|visit) a (psychiatrist|doctor|therapist|specialist) for (a )?diagnosis",
    # Chinese
    r"你(患有|得了|得了|确诊|有).{0,15}(抑郁症|焦虑症|双相|躁郁症|自闭症|多动症|强迫症|人格障碍|精神分裂|恐慌症|社交恐惧)",
    r"你(是|属于).{0,10}(抑郁|焦虑|双相|自闭|强迫|人格障碍|精神分裂)",
    r"你的(诊断|病症|疾病|障碍)是",
    r"我(诊断|判断|确定|认为)你(有|得了|患有|是)",
    r"你(符合|达到).{0,15}(诊断|症状|标准)",
    r"建议你(去)?(看|找|咨询).{0,10}(精神科|心理科|医生).{0,10}(诊断|确诊|看病)",
]

# Overreach/boundary-violation patterns: LLM promises treatment or guarantees
OVERREACH_PATTERNS: list[str] = [
    # English
    r"I (can|will) (treat|cure|heal|fix) you",
    r"you (will|are going to) (get better|be cured|recover) (if|when|by)",
    r"I (guarantee|promise) (you )?(will|can)",
    r"this (will|is going to) (definitely|certainly|guaranteed to) (work|help|cure)",
    r"you (should|must) (take|start|stop) (this )?medication",
    r"I (prescribe|recommend) (you )?(medication|medicine|drugs)",
    # Chinese
    r"我(能|可以|会)(治好|治愈|治好|帮你治)你的",
    r"你(一定|肯定会|必定会|终将)(好|康复|痊愈)",
    r"我(保证|承诺)你(一定|肯定|会)",
    r"这(一定|肯定|绝对)(能|会)(治好|帮助|缓解|有效)",
    r"你(应该|必须|需要)(吃|服用|停)(这个|些)?(药|药物)",
    r"我(给你|为你)(开|推荐|建议).{0,10}(药|药物)",
]

_DIAGNOSIS_REGEX = [re.compile(p, re.IGNORECASE) for p in DIAGNOSIS_PATTERNS]
_OVERREACH_REGEX = [re.compile(p, re.IGNORECASE) for p in OVERREACH_PATTERNS]


def _sanitize_text(text: str) -> tuple[str, bool]:
    """Remove diagnosis/overreach sentences from the reply.

    Returns (sanitized_text, was_modified).
    Instead of replacing the entire reply, we split into sentences,
    remove violating ones, and keep the rest.
    """
    was_modified = False

    # Split by newlines first, then by sentence-ending punctuation
    lines = text.split("\n")
    kept_lines: list[str] = []

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            kept_lines.append(line)
            continue

        # Check if the entire line matches any violation pattern
        is_violating = False
        for pattern in _DIAGNOSIS_REGEX + _OVERREACH_REGEX:
            if pattern.search(line):
                is_violating = True
                logger.warning(
                    "Safety reviewer: removing violating sentence: %s",
                    line_stripped[:100],
                )
                break

        if is_violating:
            was_modified = True
            # Skip this line, but keep structure by adding empty line
            kept_lines.append("")
        else:
            kept_lines.append(line)

    sanitized = "\n".join(kept_lines).strip()

    # If everything was removed, return a safe fallback
    if not sanitized:
        return "", True

    return sanitized, was_modified


def _is_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _fallback_text(user_message: str) -> str:
    if _is_chinese(user_message):
        return "我在这里陪你。我们先把节奏放慢一点，只聚焦眼前一个小步骤。"
    return "I am here with you. Let us slow this down and focus on one small next step together."


def review_response(state: GraphState) -> GraphState:
    text = state["generated_reply"].text

    # 1. Prompt leak detection (existing logic)
    has_leak = any(marker in text for marker in LEAK_MARKERS)

    # 2. Diagnosis language detection (new)
    has_diagnosis = any(p.search(text) for p in _DIAGNOSIS_REGEX)

    # 3. Overreach/promise detection (new)
    has_overreach = any(p.search(text) for p in _OVERREACH_REGEX)

    if has_leak:
        # Prompt leak: full replacement (safety critical)
        text = _fallback_text(state["user_message"])
    elif has_diagnosis or has_overreach:
        # Diagnosis/overreach: truncate violating sentences, keep the rest
        sanitized, was_modified = _sanitize_text(text)
        text = sanitized if (was_modified and sanitized) else _fallback_text(state["user_message"])

    state["generated_reply"].text = text

    if state["risk_result"].needs_crisis_mode:
        state["generated_reply"].includes_action_step = True
    return state
