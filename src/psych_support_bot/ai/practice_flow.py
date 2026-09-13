"""图内引导练习流（对话式 54321，GPT Voice 式陪伴节奏）。

与 exercise_ai.py 的关系：面板引导（/guidance）保持图外轻函数不变；
本模块是**图内**练习轮的生成层——由 practice_responder 节点调用，
每轮享受图内完整风险分类（规则 + LLM 语义兜底，强于 exercise_ai 的
纯规则筛查）与 safety_reviewer 红线审查。伦理边界不变：练习是用户
主动发起、经须知确认的交互时刻，步骤回答内容可用；UsageEvent 只记
动作元数据。

节奏契约（GPT Voice 式文字对应物）：
- 每步一问一答，先对用户上一步回答给一句自适应跟随语，再出下一步；
- 柔性处理：闻不到/尝不到时给替代方案（回忆一个味道也可以），不较真；
- 短句多气泡（≤3），与主对话的 IM 气泡渲染约定一致。

确定性边界（问卷流同款哲学）：提议/开始/暂停/恢复等流程性文本 0 LLM；
仅步进承接语与完成收尾走采样（有确定性兜底）。
"""

import logging
import re
from dataclasses import dataclass

from psych_support_bot.ai.schemas.messages import GeneratedReply, RiskResult
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.ai.tools.exercises import get_exercise_by_tag
from psych_support_bot.ai.utils.text_matching import _contains_keyword, _normalize_text
from psych_support_bot.infra.llm.generation import _invoke

logger = logging.getLogger(__name__)

# 本期唯一接入图内引导的练习 tag（机制按通用设计，扩展时只加注册项）。
PRACTICE_TAG = "panic_grounding_5_4_3_2_1"

# 步数（54321 固定 5 步：看5/触4/听3/闻2/味1）。
PRACTICE_STEP_COUNT = 5

# 同意 chip 的回传文案（服务层据此 + 须知已在上一轮展示判定同意时刻）。
PRACTICE_OFFER_MARKER = "同意并开始"
PRACTICE_PAUSE_CHIP = "先停一下"

# ---------------------------------------------------------------------------
# 意图分类（practice_responder 路由依据；规则式，零 LLM）
# ---------------------------------------------------------------------------

_PAUSE_KEYWORDS = [
    *[
        "停一下",
        "先不练",
        "先停",
        "暂停",
        "不想做了",
        "不想练",
        "缓一缓",
        "停停",
        "让我静静",
        "安静待",
        "别问我",
        "不说话了",
        "静静",
    ],
    *["let's stop", "lets stop", "stop here", "pause", "not now", "leave me alone", "be quiet"],
]
_RESUME_KEYWORDS = [
    "继续",
    "接着练",
    "接着做",
    "回来练",
    "继续吧",
    "continue",
    "resume",
    "go on",
    "keep going",
]
_RESTART_KEYWORDS = [
    "重新开始",
    "从头开始",
    "重开",
    "重来",
    "start over",
    "restart",
    "from the beginning",
]
# 练习入口意图（用户主动提出要做练习；命中即出须知+同意 chip）。
_ENTRY_KEYWORDS = [
    "54321",
    "5-4-3-2-1",
    "5 4 3 2 1",
    "接地",
    "着陆练习",
    "稳定练习",
    "带我做练习",
    "带我练",
    "grounding",
]
# 提议挂起时（上一轮 bot 消息含同意 chip 文案）的宽口径肯定词——
# 仅在 offer_pending 时生效，不会误伤普通对话里的「好/可以」。
_AFFIRM_KEYWORDS = [
    "好",
    "可以",
    "嗯",
    "行",
    "开始吧",
    "来吧",
    "好呀",
    "好的",
    "ok",
    "yes",
    "sure",
    "sounds good",
    "let's do it",
]


@dataclass
class PracticeIntent:
    """练习流意图：practice_responder 的路由依据。

    continue：步进（普通回答即继续信号）；pause：软着陆暂停；
    resume/restart：恢复/重开（paused 会话）；offer：出须知+同意 chip；
    consent：确认开始（建会话+第 1 步）；none：与练习无关（放行主链）。
    """

    kind: str


def _matches(text: str, keywords: list[str]) -> bool:
    normalized, compact = _normalize_text(text)
    return any(_contains_keyword(normalized, compact, kw) for kw in keywords)


def detect_practice_intent(
    user_message: str,
    *,
    active_practice: dict | None,
    risk_result: RiskResult,
    last_bot_reply: str = "",
) -> PracticeIntent:
    """练习流意图分类。

    优先级：active 会话 > paused 会话 > 无会话（offer/consent）。
    暂停中的会话不劫持普通聊天——只有明确的恢复/重开词才回到练习。
    """
    message = user_message or ""
    status = (active_practice or {}).get("status")

    if status == "active":
        if _matches(message, _PAUSE_KEYWORDS):
            return PracticeIntent("pause")
        if _matches(message, _RESTART_KEYWORDS):
            return PracticeIntent("restart")
        return PracticeIntent("continue")

    if status == "paused":
        if _matches(message, _RESUME_KEYWORDS):
            return PracticeIntent("resume")
        if _matches(message, _RESTART_KEYWORDS):
            return PracticeIntent("restart")
        return PracticeIntent("none")

    # 无会话：提议挂起时接受宽口径肯定词；否则仅显式入口意图。
    offer_pending = PRACTICE_OFFER_MARKER in (last_bot_reply or "")
    if offer_pending and (message.strip() == PRACTICE_OFFER_MARKER or _matches(message, _AFFIRM_KEYWORDS)):
        return PracticeIntent("consent")
    if _matches(message, _ENTRY_KEYWORDS):
        return PracticeIntent("offer")
    return PracticeIntent("none")


# ---------------------------------------------------------------------------
# 确定性文案（提议/开始/暂停/恢复轮 0 LLM）
# ---------------------------------------------------------------------------


def exercise_steps(expected_language: str) -> tuple[str, list[str]]:
    exercise = get_exercise_by_tag(PRACTICE_TAG, language="zh" if expected_language == "zh" else "en") or {}
    name = str(exercise.get("name") or PRACTICE_TAG)
    steps = [str(step) for step in (exercise.get("steps") or [])]
    return name, steps


def build_offer_reply(expected_language: str) -> tuple[str, list[dict]]:
    """练习提议轮：简介 + 完整须知 + 同意 chip（0 LLM，确定性）。

    须知全文先于确认时刻展示（与面板 intro→consent 同序）；用户点 chip
    或回「同意并开始」后，服务层才建会话（consent 即同意时刻，落
    exercise_consent 埋点）。
    """
    from psych_support_bot.domain.consents import DISCLAIMER_VERSION, EXERCISE_DISCLAIMER_EN, EXERCISE_DISCLAIMER_ZH

    zh = expected_language == "zh"
    ex_name, ex_steps = _steps_for_language(zh)
    disclaimers = EXERCISE_DISCLAIMER_ZH if zh else EXERCISE_DISCLAIMER_EN
    disclaimer_text = " ".join(f"· {line}" for line in disclaimers)
    if zh:
        text = (
            f"好，我们来做{ex_name}——用五官把注意力拉回当下，对惊慌、发紧、"
            f"飘飘忽忽的时刻都管用。一共 {len(ex_steps) or PRACTICE_STEP_COUNT} 步，一步一步来。\n\n"
            f"{disclaimer_text}\n（条款版本 {DISCLAIMER_VERSION}）\n\n"
            f"准备好了就点「{PRACTICE_OFFER_MARKER}」，或直接打这几个字。"
        )
        options = [{"label": PRACTICE_OFFER_MARKER, "send": PRACTICE_OFFER_MARKER}]
    else:
        text = (
            f"Let's do the {ex_name} — it walks your attention back to the present "
            f"through your five senses. {len(ex_steps) or PRACTICE_STEP_COUNT} steps, one at a time.\n\n"
            f"{disclaimer_text}\n(Notice version {DISCLAIMER_VERSION})\n\n"
            'Tap "Ready to begin" below when you\'re set, or just type it.'
        )
        options = [{"label": PRACTICE_OFFER_MARKER, "send": PRACTICE_OFFER_MARKER}]
    return text, options


def _steps_for_language(zh: bool) -> tuple[str, list[str]]:
    exercise = get_exercise_by_tag(PRACTICE_TAG, language="zh" if zh else "en") or {}
    name = str(exercise.get("name") or PRACTICE_TAG)
    steps = [str(step) for step in (exercise.get("steps") or [])]
    return name, steps


def build_start_reply(expected_language: str) -> str:
    """会话建立后的第 1 步指令（0 LLM，确定性——第 1 步无需承接语）。"""
    zh = expected_language == "zh"
    name, steps = _steps_for_language(zh)
    first = steps[0] if steps else ""
    if zh:
        return f"好，我们开始{name}。\n\n不用急，环顾一下四周——{first}\n\n慢慢来，我等你。"
    return f"Okay, let's begin the {name}.\n\nNo rush, look around — {first}\n\nTake your time, I'm right here."


def build_pause_reply(expected_language: str, completed_steps: int) -> str:
    zh = expected_language == "zh"
    if zh:
        return (
            f"好，练习先放在这里，刚才的 {completed_steps} 步都存了。"
            "此刻不用做任何事；想继续的时候说「继续」就行，或者聊聊现在的感觉也可以。"
        )
    return (
        f"Okay, we'll leave the exercise here — your {completed_steps} step(s) are saved. "
        'Nothing you need to do right now; say "continue" whenever you\'re ready, '
        "or just tell me how you're feeling."
    )


def build_resume_reply(expected_language: str, next_index: int) -> str:
    """恢复轮：确定性重发下一步指令（0 LLM）。"""
    zh = expected_language == "zh"
    _, steps = _steps_for_language(zh)
    step_text = steps[next_index] if 0 <= next_index < len(steps) else ""
    if zh:
        return f"好，我们接着来。\n\n{step_text}\n\n不急，我等你。"
    return f"Okay, picking up where we left off.\n\n{step_text}\n\nNo rush, I'm here."


def build_elevated_pause_reply(expected_language: str) -> str:
    """elevated 风险的软着陆暂停语（「宁暂停不漏接」，exercise_ai 同源语义）：
    练习是缓解手段，用户在练习里说出痛苦时先把人接住，而不是推着做完。"""
    zh = expected_language == "zh"
    if zh:
        return "练习先放到一边——比起做完它，现在更想先接住你的感受。你刚才说的话我听到了。想跟我聊聊现在的感觉吗？"
    return (
        "Let's set the exercise aside — right now your feelings matter more than finishing it. "
        "I heard what you just said. Would you like to tell me how you're feeling?"
    )


# ---------------------------------------------------------------------------
# LLM 步进 / 收尾生成（仅这两类轮次采样；输出仍过图内 safety_reviewer）
# ---------------------------------------------------------------------------

# transcript 每条截断长度与总预算（exercise_ai._format_transcript 同款口径）
_TRANSCRIPT_TURN_CLIP = 200
_TRANSCRIPT_BUDGET = 800


def _format_transcript(transcript: list[dict]) -> str:
    lines = []
    for turn in transcript:
        role = "用户" if turn.get("role") == "user" else "引导"
        lines.append(f"{role}: {str(turn.get('content', ''))[:_TRANSCRIPT_TURN_CLIP]}")
    return "\n".join(lines)[-_TRANSCRIPT_BUDGET:]


def split_practice_bubbles(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if len(parts) <= 1:
        return []
    if len(parts) > 3:
        parts = [*parts[:2], "\n\n".join(parts[2:])]
    return parts


def _step_prompt(
    *,
    name: str,
    step_guide: str,
    next_step_guide: str,
    step_index: int,
    total: int,
    user_reply: str,
    transcript_text: str,
    expected_language: str,
) -> str:
    zh = expected_language == "zh"
    if zh:
        return (
            f"你正在陪伴用户做「{name}」（共 {total} 步，逐步引导）。\n"
            f"刚完成的步骤（第 {step_index + 1}/{total} 步）：{step_guide}\n"
            f"用户这一步的回答：{user_reply[:300]}\n\n"
            f"对话逐字记录（时间正序）：\n{transcript_text}\n\n"
            "请生成下一条引导，要求：\n"
            "1. 先用一句话自然地接住用户刚才的回答（引用用户提到的具体事物，如「那棵树」），"
            "不评价不打分；若回答为空或说「不知道」，温和表示这完全正常。\n"
            f"2. 然后给出下一步指令：{next_step_guide}\n"
            "3. 柔性处理：如果下一步是闻/尝这类可能做不到的感官，补一句替代方案（如「回忆一个味道也可以」）。\n"
            "4. 分 2-3 个短气泡（用空行分隔），每句都短，适合逐条显示；语气像语音陪伴，不急不催。\n"
            "5. 不诊断、不建议就医、不出现任何临床术语；结尾不提问。"
        )
    return (
        f"You are guiding the user through the {name} ({total} steps, one at a time).\n"
        f"Step just completed ({step_index + 1}/{total}): {step_guide}\n"
        f"User's answer for this step: {user_reply[:300]}\n\n"
        f"Verbatim transcript (chronological):\n{transcript_text}\n\n"
        "Generate the next guidance message:\n"
        "1. First, one sentence that naturally picks up the user's answer (mention the specific "
        "thing they named); no evaluation, no scoring. If the answer is empty or 'I don't know', "
        "gently affirm that's completely fine.\n"
        f"2. Then give the next step: {next_step_guide}\n"
        "3. Flexible handling: if the next step is smell/taste (may be impossible), add an "
        "alternative (e.g. 'recalling a scent works too').\n"
        "4. Split into 2-3 short bubbles (separated by blank lines), short sentences, "
        "voice-companion pacing, unhurried.\n"
        "5. No diagnosis, no clinical terms, no medical advice; end without a question."
    )


def _completion_prompt(*, name: str, transcript_text: str, expected_language: str) -> str:
    zh = expected_language == "zh"
    if zh:
        return (
            f"用户刚刚做完「{name}」的全部步骤。对话逐字记录（时间正序）：\n{transcript_text}\n\n"
            "请生成收尾回应，要求：\n"
            "1. 肯定他完成了整个练习（不夸张、不评判）。\n"
            "2. 问一句此刻身体/情绪和练习前比有没有一点不同（开放、轻，允许说没有）。\n"
            "3. 分 2 个短气泡（空行分隔）；不诊断、不出现临床术语。"
        )
    return (
        f"The user just finished all steps of the {name}. Verbatim transcript (chronological):\n{transcript_text}\n\n"
        "Generate a closing response:\n"
        "1. Acknowledge completing the whole exercise (no exaggeration, no judgment).\n"
        "2. Ask one open, light question: does anything feel even slightly different in body or "
        "mood compared with before (it's fine to say no).\n"
        "3. Two short bubbles (blank-line separated); no diagnosis, no clinical terms."
    )


def generate_step_reply(
    state: GraphState,
    *,
    step_index: int,
    user_reply: str,
    expected_language: str,
) -> str:
    """步进轮 LLM 生成（接住上一步回答 + 出下一步）。失败回退确定性指令。"""
    zh = expected_language == "zh"
    name, steps = _steps_for_language(zh)
    total = len(steps) or PRACTICE_STEP_COUNT
    next_index = step_index + 1
    step_guide = steps[step_index] if 0 <= step_index < len(steps) else ""
    next_step_guide = steps[next_index] if 0 <= next_index < len(steps) else ""
    transcript = list((state.get("active_practice") or {}).get("transcript") or [])
    transcript_text = _format_transcript(transcript)

    system_prompt = (
        "你是一次陪伴式心理练习的引导者（voice-companion 风格）：温暖、简短、"
        "一步一步、绝不催促。你的输出会以 IM 气泡逐条显示。"
        if zh
        else "You are a voice-companion style guide for a grounding exercise: warm, brief, "
        "step by step, never rushing. Your output renders as IM bubbles."
    )
    user_prompt = _step_prompt(
        name=name,
        step_guide=step_guide,
        next_step_guide=next_step_guide,
        step_index=step_index,
        total=total,
        user_reply=user_reply,
        transcript_text=transcript_text,
        expected_language=expected_language,
    )

    def deterministic_fallback() -> str:
        if zh:
            body = f"收到。\n\n{next_step_guide}" if next_step_guide else "收到。"
            return f"{body}\n\n不急，慢慢来。"
        body = f"Noted.\n\n{next_step_guide}" if next_step_guide else "Noted."
        return f"{body}\n\nNo rush."

    try:
        return _invoke(
            system_prompt,
            user_prompt,
            expected_language,
            mode="intervention",
            fallback=deterministic_fallback,
        )
    except Exception:
        logger.warning("Practice step LLM failed; deterministic fallback served.", exc_info=True)
        return deterministic_fallback()


def generate_completion_reply(state: GraphState, *, expected_language: str) -> str:
    """完成轮收尾（1 LLM）。失败回退确定性收尾。"""
    zh = expected_language == "zh"
    name, _ = _steps_for_language(zh)
    transcript = list((state.get("active_practice") or {}).get("transcript") or [])
    transcript_text = _format_transcript(transcript)

    system_prompt = (
        "你是一次陪伴式心理练习的引导者（voice-companion 风格）：温暖、简短、不评判。"
        if zh
        else "You are a voice-companion style guide: warm, brief, non-judgmental."
    )
    user_prompt = _completion_prompt(name=name, transcript_text=transcript_text, expected_language=expected_language)

    def deterministic_fallback() -> str:
        if zh:
            return (
                f"好，{name}五步都走完了。刚才你一个一个说下来，这就是把它做完。\n\n"
                "现在身体和情绪有没有一点不一样？没有变化也完全可以。"
            )
        return (
            f"Done — all five steps of the {name}. Saying each one out loud is completing it.\n\n"
            "Does anything feel even a little different now? It's completely fine if not."
        )

    try:
        return _invoke(
            system_prompt,
            user_prompt,
            expected_language,
            mode="intervention",
            fallback=deterministic_fallback,
        )
    except Exception:
        logger.warning("Practice completion LLM failed; deterministic fallback served.", exc_info=True)
        return deterministic_fallback()


def build_generated_reply(state: GraphState, reply_text: str) -> None:
    """写回 GraphState.generated_reply（mode=intervention，低风险轮拆多气泡）。"""
    state["generated_reply"] = GeneratedReply(
        text=reply_text,
        style="intervention",
        messages=split_practice_bubbles(reply_text),
    )


__all__ = [
    "PRACTICE_OFFER_MARKER",
    "PRACTICE_PAUSE_CHIP",
    "PRACTICE_STEP_COUNT",
    "PRACTICE_TAG",
    "PracticeIntent",
    "build_elevated_pause_reply",
    "build_generated_reply",
    "build_offer_reply",
    "build_pause_reply",
    "build_resume_reply",
    "build_start_reply",
    "detect_practice_intent",
    "generate_completion_reply",
    "generate_step_reply",
    "split_practice_bubbles",
]
