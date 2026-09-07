import logging
import re

from psych_support_bot.ai.prompts.templates import (
    build_boundary_base_prompt,
    build_boundary_state_prompt,
    build_knowledge_block_prompt,
    build_memory_block_prompt,
    build_mode_shape_prompt,
    build_role_prompt,
    build_system_guidance,
)
from psych_support_bot.ai.safety.crisis import build_crisis_reply
from psych_support_bot.ai.schemas.messages import RiskResult
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output

logger = logging.getLogger(__name__)


def _build_leak_markers() -> tuple[str, ...]:
    role_text = build_role_prompt()
    boundary_text = build_boundary_base_prompt() + build_boundary_state_prompt(risk_level="low")
    context_text = build_memory_block_prompt(memory_summary="") + build_knowledge_block_prompt(knowledge_context="")
    output_text = build_mode_shape_prompt(mode="support", risk_level="low")
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

# ---------------------------------------------------------------------------
# Vendor/model-name leak (identity guard): the assistant must never surface
# the underlying model or vendor name. Langfuse 巡检（2026-09-04）：用户问
# 「你是什么模型」时回复自称 "dots，由小红书 Dots Studio 开发"。提示词层
# 已锁定口径（build_identity_prompt），此处是运行时兜底——命中即整条替换。
# Word-boundary regexes so "dots" doesn't match ordinary English words, and
# plain substring checks for CJK names that have no word boundaries.
# ---------------------------------------------------------------------------
VENDOR_NAME_REGEXES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # "connect/join the dots" is ordinary English — exclude it explicitly.
        r"(?<!the )\bdots\b",
        r"\bdots\s*studio\b",
        r"\bchatgpt\b",
        r"\bgpt-?\d",
        r"\bopenai\b",
        r"\bclaude\b",
        r"\banthropic\b",
        r"\bgemini\b",
        r"\bdeepseek\b",
        r"\bqwen\b",
        r"\bqwq\b",
        r"\bkimi\b",
        r"\bmoonshot\b",
        r"\bllama\b",
        r"\bdoubao\b",
        r"\bernie\b",
        r"\bchatglm\b",
        r"\bgpt\b",
    )
)
VENDOR_NAME_SUBSTRINGS: tuple[str, ...] = (
    "智谱",
    "深度求索",
    "月之暗面",
    "通义千问",
    "文心一言",
    "小红书",
)

IDENTITY_FALLBACK_ZH = "我是这个应用里的 AI 心理支持伙伴，一个愿意听你说话的 AI，不是真人。有什么想聊的，我都在。"
IDENTITY_FALLBACK_EN = "I'm this app's AI support companion — an AI here to listen, not a human. What's on your mind?"


def _detect_vendor_name(text: str) -> bool:
    return any(p.search(text) for p in VENDOR_NAME_REGEXES) or any(s in text for s in VENDOR_NAME_SUBSTRINGS)


# ---------------------------------------------------------------------------
# Internal clinical-label leakage: consultation agents write opinions in the
# 观察/形成/下一步 format and the reply skeleton uses 回应/工作性假设/下一问;
# these are internal scaffolding and must never reach the user. Runs as a
# line-level cleanup AFTER leak/redline checks (those replace wholesale;
# here we strip scaffolding but keep legitimate content).
# 内部 formulation 标签（观察/形成/下一步 + 英文 Observation/Formulation/
# Next step）整行都是内部内容 —— 删行；可见回复骨架标签（回应/工作性假设/
# 下一问 + Reflection/Working hypothesis/Next question）只剥前缀、保留正文。
# 只有标签后跟冒号、或标签独占一行（标题式，如「# 观察 用户希望…」）才
# 判定，避免把恰好以这些词开头的普通句子误删（如「我们聊聊下一步怎么办」）。
# ---------------------------------------------------------------------------
_INTERNAL_LABELS = ("观察", "形成", "下一步", "Observation", "Formulation", "Next step")
_VISIBLE_LABELS = ("回应", "工作性假设", "下一问", "Reflection", "Working hypothesis", "Next question")

# Heading style: 「# 观察 …」 / 「**观察**」独占一行 —— 整行都是内部内容
_INTERNAL_LABEL_LINE_RE = re.compile(
    r"^\s*#{1,6}\s*(?:\*\*)?\s*(?:" + "|".join(_INTERNAL_LABELS) + r")\b(?:\*\*)?\s*[:：]?",
    re.IGNORECASE,
)
# Colon style: 「观察：…」 / 「**形成**: …」 —— 标签引出内部 formulation
_INTERNAL_LABEL_COLON_RE = re.compile(
    r"^\s*(?:\d{1,2}[.、)]\s*)?(?:\*\*)?\s*(?:" + "|".join(_INTERNAL_LABELS) + r")\s*(?:\*\*)?\s*[:：]",
    re.IGNORECASE,
)
# Visible reply skeleton labels: strip the prefix, keep the content
_VISIBLE_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*(?:" + "|".join(_VISIBLE_LABELS) + r")\s*(?:\*\*)?\s*[:：]\s*",
    re.IGNORECASE,
)


def _sanitize_internal_labels(text: str) -> tuple[str, bool]:
    """Strip internal clinical scaffolding from the visible reply.

    Lines that ARE internal-formulation content (观察/形成/下一步/…) are removed
    entirely; lines merely PREFIXED with a visible-reply label (回应：… /
    **工作性假设**：…) keep their content with the label removed.

    Returns (cleaned_text, was_modified). Empty result signals the caller to
    use the fallback reply.
    """
    was_modified = False
    kept_lines: list[str] = []
    for line in text.split("\n"):
        if _INTERNAL_LABEL_LINE_RE.match(line) or _INTERNAL_LABEL_COLON_RE.match(line):
            was_modified = True
            logger.warning("Safety reviewer: removing internal-formulation line: %s", line.strip()[:100])
            continue
        new_line, n = _VISIBLE_LABEL_PREFIX_RE.subn("", line, count=1)
        if n:
            was_modified = True
            logger.warning("Safety reviewer: stripped visible-reply label prefix: %s", line.strip()[:100])
        kept_lines.append(new_line)
    cleaned = "\n".join(kept_lines).strip()
    return (cleaned, was_modified) if was_modified else (text, False)


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

# Challenge/questioning patterns: LLM outputs that push the user with
# confrontational or probing questions. These are inappropriate when
# challenge_allowed is False (e.g. engagement, exploration, safety_stabilization).
CHALLENGE_PATTERNS: list[str] = [
    # English - confrontational questions
    r"are you sure",
    r"do you really think",
    r"why (don't|do) you",
    r"why (not|would|should) you",
    r"have you considered",
    r"have you thought about",
    r"what makes you think",
    r"but (don't|doesn't) you",
    r"isn't it true that",
    # English - directive/probing
    r"you (should|need to|must) (ask yourself|think about|consider)",
    r"let me (push back|challenge|question)",
    # Chinese - confrontational questions
    r"你确定",
    r"你真的(认为|觉得|想)",
    r"你为什么不",
    r"你为什么",
    r"你有没有(想过|考虑过)",
    r"是什么让你(觉得|认为)",
    r"你有没有想过",
    r"你不觉得",
    r"难道不是",
    # Chinese - directive/probing
    r"你(应该|需要|必须)(问问自己|想想|考虑一下)",
    r"让我(质询|挑战|反问|质疑)一下",
]

# ---------------------------------------------------------------------------
# P0-1: Pathological attribution patterns
# LLM attributes the user's experience to a pathological brain/perception
# system malfunction.  These must NOT match normal psychoeducation that
# explains mechanisms without pathologising (e.g. "anxiety makes sensations
# feel stronger" is fine; "your brain is distorting your perception" is not).
# ---------------------------------------------------------------------------
PATHOLOGICAL_ATTRIBUTION_PATTERNS: list[str] = [
    # English — brain/system pathology
    r"your (brain|mind|nervous system|perception system) (is|are) (distorting|deceiving|tricking|malfunctioning|broken|faulty|misfiring)",
    r"your (brain|mind) (is )?(playing tricks|playing games|messing|tricking) (on|with) you",
    r"your (brain|mind) (is )?(sending|firing) (false|wrong|incorrect) (signals|alarms|messages)",
    # English — perception unreality attributed to pathology
    r"your (perception|perceptions|senses) (is|are) (not )?(reliable|trustworthy|accurate|functioning properly)",
    r"your (perception|senses) (is|are) (distorted|impaired|corrupted|warped)",
    # Chinese — brain/system pathology
    r"你的(大脑|神经系统|感知系统|精神系统)(在)?(扭曲|欺骗|篡改|错乱|出错|故障|失灵|紊乱)",
    r"你的(大脑|脑部)(在)?(欺骗|误导|捉弄|戏弄)你",
    r"你的(大脑|脑部)(在)?发出(错误|虚假)的(信号|警报)",
    r"你的(大脑|脑部)(在)?(误报|误警)",
    # Chinese — perception unreality attributed to pathology
    r"你的(感知|感觉|感知系统)(是|处于)?(扭曲|失真|不可靠|不准确|不正常|出了问题|有缺陷)",
]

# ---------------------------------------------------------------------------
# P0-2: Subjective-experience denial patterns
# LLM directly denies the reality/existence of what the user reports
# experiencing (seeing, hearing, feeling).  Must NOT match statements that
# acknowledge subjective reality while explaining its relationship to
# external reality (e.g. "these feelings are real to you" is fine).
# ---------------------------------------------------------------------------
EXPERIENCE_DENIAL_PATTERNS: list[str] = [
    # English — direct denial of perception
    r"what you (see|hear|feel|sense) (is|isn't|is not|are|aren't|are not) (real|true|realistic|actually (there|happening))",
    r"(those|these|the) (voices|sounds|images|visions|things) (you (hear|see|describe) )?(are|is) (not|n't) (real|true|actually (there|happening))",
    r"(it|that|this)(is|'s| is)( just| nothing but)? your (imagination|fantasy|delusion|mind playing tricks)",
    r"(nothing|nobody|no one|there)('s| is| are)? (really|actually) (there|watching|following|happening)",
    # English — denial of feeling reality
    r"your (feelings|emotions|reactions) (are|aren't|are not) (real|valid|justified|grounded)",
    # Chinese — direct denial of perception
    r"你(看到|听到|感觉到|感知到)的(东西|事物|声音|画面|感觉)(不是|并不|根本不)(真实的|真的|存在的|现实的)",
    r"(那些|这些)(声音|画面|东西|事物)(不是|并不|根本不)(真实的|真的|存在的)",
    r"(那|这)(只是|不过是|无非是)你的(想象|幻想|臆想|幻觉|心理作用)",
    r"(没有|根本没有|其实没有)(人|谁)(在)(看着|监视|跟踪|跟着)你",
    r"(没有|根本没有|其实没有)(什么|什么东西)(真的|真的在)(发生|存在)",
    # Chinese — denial of feeling reality
    r"你的(感受|情绪|反应)(不是|并不|根本不)(真实的|真的|合理的|有依据的)",
]

# ---------------------------------------------------------------------------
# P0-3: Over-pathologization label patterns
# LLM uses clinical diagnostic labels to categorise the user's experience
# (e.g. "this is a hallucination", "what you're describing is a delusion").
# Must NOT match when the LLM quotes the user's own words or asks clarifying
# questions about experiences without labelling them.
# ---------------------------------------------------------------------------
OVER_PATHOLOGIZATION_PATTERNS: list[str] = [
    # English — labelling experiences as clinical phenomena
    r"(this|that|what you('re| are)( describing| experiencing)|these (experiences|symptoms)) (is|are|sounds? like|seems? like|appears? to be) (a |an )?(hallucination|delusion|psychotic symptom|psychosis|dissociation|dissociative episode|paranoia|paranoid (delusion|belief)|psychotic break|mental break)",
    r"you('re| are) (experiencing|having|suffering from) (a |an )?(hallucination|delusion|psychotic episode|dissociative episode|psychotic break)",
    r"this (is|sounds like|seems like) (a |an )?(psychotic|psychiatric|mental) (symptom|disorder|condition|episode|break)",
    # Chinese — labelling experiences as clinical phenomena
    r"(这|那|你描述的|你经历的|你体验到的)(是|属于|像是|看起来是|听起来是|似乎是)(幻觉|妄想|精神病性症状|精神分裂症状|解离|解离症状|偏执|偏执妄想|精神崩溃)",
    r"你(正在|在)(经历|产生|出现)(幻觉|妄想|精神病性症状|解离症状|精神崩溃)",
    r"这(是|属于|像是|看起来是)(精神病性|精神科|心理疾病)的(症状|障碍|表现|发作)",
]


def _compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


# 规则表（P3 收敛）：六个违规家族共用同一"逐行清除 + 过渡句"逻辑，
# 差异仅在正则组与日志标签——新增家族只需在此登记。
_REDACTION_REGEX_GROUPS: dict[str, list[re.Pattern]] = {
    "challenge": _compile_patterns(CHALLENGE_PATTERNS),
    "diagnosis": _compile_patterns(DIAGNOSIS_PATTERNS),
    "overreach": _compile_patterns(OVERREACH_PATTERNS),
    "pathological_attribution": _compile_patterns(PATHOLOGICAL_ATTRIBUTION_PATTERNS),
    "experience_denial": _compile_patterns(EXPERIENCE_DENIAL_PATTERNS),
    "over_pathologization": _compile_patterns(OVER_PATHOLOGIZATION_PATTERNS),
}

_CHALLENGE_REGEX = _REDACTION_REGEX_GROUPS["challenge"]

# All red-line regexes combined for unified sanitisation in _sanitize_text().
_ALL_REDLINED_REGEX = (
    _REDACTION_REGEX_GROUPS["diagnosis"]
    + _REDACTION_REGEX_GROUPS["overreach"]
    + _REDACTION_REGEX_GROUPS["pathological_attribution"]
    + _REDACTION_REGEX_GROUPS["experience_denial"]
    + _REDACTION_REGEX_GROUPS["over_pathologization"]
)

# Transition phrases inserted at truncation points to maintain coherence.
_TRANSITION_ZH = "我听到你说的了，我们继续。"
_TRANSITION_EN = "I hear what you're saying, let's continue."
_TRANSITION_ZH_CRISIS = "我在这里陪着你，我们先关注你的安全。"
_TRANSITION_EN_CRISIS = "I'm here with you, let's focus on your safety first."

_ALL_TRANSITION_PHRASES = frozenset(
    {
        _TRANSITION_ZH,
        _TRANSITION_EN,
        _TRANSITION_ZH_CRISIS,
        _TRANSITION_EN_CRISIS,
    }
)


def _is_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _get_transition_phrase(needs_crisis_mode: bool, expected_language: str) -> str:
    """Return a safe transition phrase for truncation points.

    Selects by crisis/non-crisis context and expected language.
    """
    lang = expected_language or "en"
    if needs_crisis_mode:
        return _TRANSITION_ZH_CRISIS if lang == "zh" else _TRANSITION_EN_CRISIS
    return _TRANSITION_ZH if lang == "zh" else _TRANSITION_EN


def _remove_violating_lines(
    text: str,
    patterns: list[re.Pattern],
    *,
    log_label: str,
    needs_crisis_mode: bool = False,
    expected_language: str = "",
) -> tuple[str, bool]:
    """逐行清除命中 patterns 的句子，在首个截断点插入过渡句保持连贯。

    Returns (sanitized_text, was_modified)。若全部内容被清除（或只剩
    过渡句），返回 ("", True) 提示调用方走完整 fallback。六个违规家族
    共用此实现（P3 收敛，原 _sanitize_text/_sanitize_challenge 同构复制）。
    """
    was_modified = False

    lines = text.split("\n")
    kept_lines: list[str] = []
    # Track whether a transition phrase has already been inserted
    # to avoid stacking multiple transitions.
    transition_inserted = False

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            kept_lines.append(line)
            continue

        is_violating = any(pattern.search(line) for pattern in patterns)
        if is_violating:
            was_modified = True
            logger.warning("Safety reviewer: removing %s sentence: %s", log_label, line_stripped[:100])
            # Insert a transition phrase once at the first truncation point
            # to avoid a jarring gap, then leave subsequent truncations blank.
            if not transition_inserted:
                transition = _get_transition_phrase(needs_crisis_mode, expected_language)
                kept_lines.append(transition)
                transition_inserted = True
            else:
                kept_lines.append("")
        else:
            kept_lines.append(line)

    sanitized = "\n".join(kept_lines).strip()

    # If everything was removed (or only a transition phrase remains),
    # signal that a full fallback is needed.
    if not sanitized or (transition_inserted and sanitized in _ALL_TRANSITION_PHRASES):
        return "", True

    return sanitized, was_modified


def _sanitize_text(
    text: str,
    *,
    needs_crisis_mode: bool = False,
    expected_language: str = "",
) -> tuple[str, bool]:
    """Remove red-line violating sentences from the reply.

    Checks against all red-line regex groups: diagnosis, overreach,
    pathological attribution, experience denial, and over-pathologization.
    """
    return _remove_violating_lines(
        text,
        _ALL_REDLINED_REGEX,
        log_label="red-line violating",
        needs_crisis_mode=needs_crisis_mode,
        expected_language=expected_language,
    )


def _fallback_text(
    user_message: str = "",
    expected_language: str = "",
    *,
    risk_result: RiskResult | None = None,
) -> str:
    """Return a safe fallback reply.

    When *risk_result* indicates crisis mode, delegates to
    build_crisis_reply() which includes hotline resources.
    Otherwise returns a grounding phrase.
    """
    if risk_result is not None and risk_result.needs_crisis_mode:
        return build_crisis_reply(
            risk_result,
            user_message=user_message,
            expected_language=expected_language,
        )
    lang = expected_language or ("zh" if _is_chinese(user_message) else "en")
    if lang == "zh":
        return "我在这里陪你。我们先把节奏放慢一点，只聚焦眼前一个小步骤。"
    return "I am here with you. Let us slow this down and focus on one small next step together."


def _detect_challenge(text: str) -> bool:
    """Check if the reply contains confrontational or probing language."""
    return any(p.search(text) for p in _CHALLENGE_REGEX)


def _sanitize_challenge(
    text: str,
    *,
    needs_crisis_mode: bool = False,
    expected_language: str = "",
) -> tuple[str, bool]:
    """Remove challenge/confrontation sentences from the reply.

    Removes sentences containing challenge patterns, inserts a transition
    phrase at the first truncation point, and keeps the rest.
    """
    return _remove_violating_lines(
        text,
        _CHALLENGE_REGEX,
        log_label="challenge (challenge_allowed=False)",
        needs_crisis_mode=needs_crisis_mode,
        expected_language=expected_language,
    )


def _detect_redline(text: str) -> bool:
    """Check if the text contains any red-line violation.

    Combines diagnosis, overreach, pathological attribution, experience
    denial, and over-pathologization checks.
    """
    return any(p.search(text) for p in _ALL_REDLINED_REGEX)


def review_response(state: GraphState) -> GraphState:
    with trace_span(
        "node.safety_reviewer",
        input={
            "reply_text": state["generated_reply"].text[:200],
            "challenge_allowed": state.get("challenge_allowed", False),
        },
    ) as obs:
        text = state["generated_reply"].text
        risk_result = state["risk_result"]
        needs_crisis = risk_result.needs_crisis_mode
        expected_lang = state.get("expected_language", "")

        # 1. Prompt leak detection (existing logic)
        has_leak = any(marker in text for marker in LEAK_MARKERS)

        # 1b. Vendor/model-name leak (identity guard): full replacement —
        #     same path as prompt leaks, safety-critical.
        vendor_leak = _detect_vendor_name(text)

        # 2. Red-line detection: diagnosis, overreach, pathological attribution,
        #    experience denial, over-pathologization (all unified).
        has_redline = _detect_redline(text)

        # 3. B2.3: Challenge review — when challenge_allowed is False, detect
        #    and remove confrontational/probing language.
        challenge_allowed = state.get("challenge_allowed", False)
        has_challenge = False
        if not challenge_allowed:
            has_challenge = _detect_challenge(text)

        # 4. Internal clinical scaffolding (观察/形成/下一步/回应 labels):
        #    line-level cleanup that keeps legitimate content.
        labeled_text, had_labels = _sanitize_internal_labels(text)

        if has_leak or vendor_leak:
            # Prompt/vendor leak: full replacement (safety critical)
            if vendor_leak:
                lang = expected_lang or ("zh" if _is_chinese(state["user_message"]) else "en")
                text = IDENTITY_FALLBACK_ZH if lang == "zh" else IDENTITY_FALLBACK_EN
            else:
                text = _fallback_text(
                    state["user_message"],
                    expected_lang,
                    risk_result=risk_result,
                )
        elif had_labels:
            text = (
                labeled_text
                if labeled_text
                else _fallback_text(
                    state["user_message"],
                    expected_lang,
                    risk_result=risk_result,
                )
            )
        elif has_redline:
            # Red-line violations: truncate violating sentences, keep the rest
            sanitized, was_modified = _sanitize_text(
                text,
                needs_crisis_mode=needs_crisis,
                expected_language=expected_lang,
            )
            text = (
                sanitized
                if (was_modified and sanitized)
                else _fallback_text(
                    state["user_message"],
                    expected_lang,
                    risk_result=risk_result,
                )
            )
        elif has_challenge:
            # Challenge in non-challenge-allowed context: remove challenge sentences
            sanitized, was_modified = _sanitize_challenge(
                text,
                needs_crisis_mode=needs_crisis,
                expected_language=expected_lang,
            )
            text = (
                sanitized
                if (was_modified and sanitized)
                else _fallback_text(
                    state["user_message"],
                    expected_lang,
                    risk_result=risk_result,
                )
            )

        state["generated_reply"].text = text

        update_span_output(
            obs,
            {
                "has_leak": has_leak,
                "vendor_leak": vendor_leak,
                "had_internal_labels": had_labels,
                "has_redline": has_redline,
                "has_challenge": has_challenge,
                "modified": has_leak or vendor_leak or had_labels or has_redline or has_challenge,
                "final_text": text[:200],
            },
        )
    return state
