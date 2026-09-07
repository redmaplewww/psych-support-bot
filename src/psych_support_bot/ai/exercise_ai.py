"""练习场景 AI 生成层：完成反馈 + 完成后对话引导。

与主对话图的关系：练习引导/反馈是"单次练习内"的短交互，独立轻函数，
不进 conversation graph（不污染跨轮会话状态）。

安全接入（与 chat 同步强度——用户决策 2026-09-04）：
- 引导/反馈的输入（用户消息 + 步骤回答全文拼接）先过规则风险分类
  （classify_message_risk，零延迟）；elevated 及以上不生成常规引导——
  返回危机安抚口径（build_crisis_reply），引导/反馈会话标记 paused。
- 生成回复过 safety_reviewer 的红线检测纯函数（_detect_redline），
  命中即替换为过渡语（与图内 review_response 同源语义）。

伦理边界（重画于 2026-09-04，须知同意生效后）：
- 被动批量分析（M1/M2/M3 历史趋势）继续不读内容——旧边界不变；
- 练习引导/反馈是用户主动发起、经隐私与数据处理协议明示同意的
  交互时刻，步骤回答内容可用；UsageEvent 埋点仍只记元数据。
"""

import logging
import re

from psych_support_bot.ai.knowledge.index import retrieve_knowledge_entries
from psych_support_bot.ai.nodes.safety_reviewer import _detect_redline
from psych_support_bot.ai.prompts.templates import build_role_prompt
from psych_support_bot.ai.safety.crisis import build_crisis_reply
from psych_support_bot.ai.safety.rules import classify_message_risk
from psych_support_bot.ai.schemas.messages import RiskResult
from psych_support_bot.infra.llm.generation import _invoke

logger = logging.getLogger(__name__)

# 红线命中后的替换语（与图内 _TRANSITION_ZH 同源口径，练习场景措辞）
_REDLINE_REPLACEMENT_ZH = "我听到你说的了。我们慢慢来，先照顾好此刻的感受。"
_REDLINE_REPLACEMENT_EN = "I hear you. Let's take this gently and take care of how you feel right now."

# 引导回复的 message 数上限（前端气泡渲染约定与主对话一致）
_GUIDANCE_MAX_BUBBLES = 3


def _split_guidance_messages(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if len(parts) <= 1:
        return []
    if len(parts) > _GUIDANCE_MAX_BUBBLES:
        parts = [*parts[:2], "\n\n".join(parts[2:])]
    return parts


def _screen_risk(texts: list[str]) -> RiskResult:
    """对练习场景的输入文本集合做规则风险筛查（与 chat 规则层同源）。

    LLM 语义兜底在练习场景有意省略：规则层对显性危机词的覆盖经 P0-7
    对齐已足够拦截显性信号；引导轮的后续消息每轮都会再过本函数，
    语义层漏网的渐进表述会在下一轮被规则层捕获。elevated 即暂停引导
    （宁暂停不漏接）。
    """
    combined = "\n".join(t for t in texts if t)
    return classify_message_risk(combined)


def _sanitize_output(text: str, expected_language: str) -> str:
    if _detect_redline(text):
        logger.warning("Exercise AI output hit redline; replacing with transition line.")
        return _REDLINE_REPLACEMENT_ZH if expected_language != "en" else _REDLINE_REPLACEMENT_EN
    return text


def _risk_pause_reply(risk: RiskResult, user_message: str, expected_language: str) -> str:
    return build_crisis_reply(risk, user_message=user_message, expected_language=expected_language)


def _format_transcript(transcript: list[dict]) -> str:
    lines = []
    for turn in transcript:
        role = "用户" if turn.get("role") == "user" else "引导"
        lines.append(f"{role}: {str(turn.get('content', ''))[:200]}")
    return "\n".join(lines)


def generate_exercise_feedback(
    *,
    exercise_name: str,
    exercise_description: str,
    step_guides: list[str],
    step_responses: list[str],
    expected_language: str,
) -> tuple[str, str]:
    """完成后 AI 个人化反馈。

    Returns:
        (feedback_text, generated_by)——generated_by 为 "llm" 或
        "safety_pause"（风险拦截，反馈为危机安抚口径）或 "fallback"
        （LLM 不可用，确定性鼓励语）。
    """
    risk = _screen_risk([*step_responses])
    if risk.risk_level in {"high", "critical"} or risk.needs_crisis_mode:
        return (
            _risk_pause_reply(risk, user_message="\n".join(step_responses), expected_language=expected_language),
            "safety_pause",
        )
    # elevated 也暂停常规反馈口径（内容里可能有未展开的痛苦信号），但
    # 反馈本身仍由危机模板承载（elevated 的 build_crisis_reply 是软着陆口径）。
    if risk.risk_level == "elevated":
        return (
            _risk_pause_reply(risk, user_message="\n".join(step_responses), expected_language=expected_language),
            "safety_pause",
        )

    pairs = []
    for i, (guide, resp) in enumerate(zip(step_guides, step_responses, strict=False), start=1):
        if resp.strip():
            pairs.append(f"步骤 {i}（{guide[:60]}）：{resp[:300]}")
    answers_text = "\n".join(pairs) or "（用户未填写步骤内容）"

    zh = expected_language == "zh"
    system_prompt = "\n\n".join(
        [
            build_role_prompt(),
            (
                f"用户刚完成自助练习「{exercise_name}」。{exercise_description} "
                "以下是用户在各步骤写下的回答。请生成一段针对这次练习的个人化反馈：\n"
                "1. 先用一两句直接引用用户自己的话或意象，确认ta写下的内容被认真看见了；\n"
                "2. 从这个练习的视角给出一个温和、非诊断的观察（练习是 "
                + exercise_name
                + "，不是量表，不要打分或诊断）；\n"
                "3. 给一步很小的下一步建议（可以是今天的一个微行动，或下次练什么）。\n"
                "约束：全文 120 字以内、2-3 个短段；不罗列、不用小标题、不用临床术语；"
                "不重复罗列用户的回答原文；不用'根据资料/研究表明'这类措辞。"
            ),
        ]
    )
    user_content = f"用户在练习中的回答：\n{answers_text}"
    if not zh:
        system_prompt = (
            "The user just completed the self-help exercise '"
            + exercise_name
            + "'. "
            + exercise_description
            + " Generate a personalized feedback: (1) reflect their own words/imagery so they feel heard; "
            "(2) one gentle non-diagnostic observation from this exercise's perspective — never score or diagnose; "
            "(3) one tiny next step (a micro-action today, or what to practice next). "
            "Keep it under 90 words, 2-3 short paragraphs, no lists, no headings, no clinical jargon, "
            "no 'according to research' phrasing.\n\n"
            + user_content.replace("用户在练习中的回答：", "User's step answers:")
        )

    def _deterministic_fallback() -> str:
        if zh:
            return (
                f"你把「{exercise_name}」完整走完了，这几步里的每一笔都是你为自己的心绪做的努力。"
                "带着刚才写下的感受，今天给自己一点缓冲的时间；想继续时，这个练习或同类里的另一个都欢迎你随时回来。"
            )
        return (
            f"You made it all the way through '{exercise_name}' — every step you wrote down was "
            "an effort for your own well-being. Give yourself some buffer time today; this exercise "
            "or a sibling one is here whenever you want to return."
        )

    try:
        feedback = _invoke(
            system_prompt, user_content, expected_language, mode="support", fallback=_deterministic_fallback
        )
        return _sanitize_output(feedback, expected_language), "llm"
    except Exception:
        logger.exception("Exercise feedback generation failed; serving deterministic fallback.")
        return _deterministic_fallback(), "fallback"


def generate_exercise_guidance(
    *,
    exercise_name: str,
    exercise_description: str,
    current_step_index: int,
    step_guide: str,
    step_responses: list[str],
    user_message: str,
    dialog_history: list[dict],
    expected_language: str,
) -> tuple[str, str]:
    """练习中/完成后的对话引导（单步内多轮，P2）。

    Returns:
        (reply_text, status)——status ∈ {"ok", "risk_paused"}。
        risk_paused 时 reply 为危机安抚口径，前端应停止引导并展示资源。
    """
    risk = _screen_risk([user_message, *step_responses])
    if risk.risk_level in {"high", "critical"} or risk.needs_crisis_mode or risk.risk_level == "elevated":
        return _risk_pause_reply(risk, user_message=user_message, expected_language=expected_language), "risk_paused"

    knowledge_hits = retrieve_knowledge_entries(user_message, mode="intervention", risk_level="low", limit=2)
    knowledge_text = " ".join(f"{e.title}: {e.summary}" for e in knowledge_hits)

    zh = expected_language == "zh"
    history_text = _format_transcript(dialog_history)
    system_prompt = "\n\n".join(
        [
            build_role_prompt(),
            (
                f"你正在陪伴用户做自助练习「{exercise_name}」。{exercise_description}\n"
                f"当前步骤（第 {current_step_index + 1} 步）：{step_guide[:120]}\n"
                + (
                    f"用户此前步骤的回答：{chr(10).join(r[:150] for r in step_responses if r.strip())[:600]}\n"
                    if any(r.strip() for r in step_responses)
                    else ""
                )
                + (f"本次引导对话至今：\n{history_text[:800]}\n" if history_text else "")
                + (f"相关背景（化用，不引用）：{knowledge_text[:400]}\n" if knowledge_text else "")
                + "\n引导要求：用户此刻的表达往往模糊（如'被误解了，心里不是滋味'）。你的任务：\n"
                "1. 先共情命名这种混合感受（把说不清的滋味说清楚一点），用用户自己的词；\n"
                "2. 一次只问一个澄清式问题，帮用户把模糊感受落到具体（哪句话/哪个瞬间/身体哪里有感觉）；\n"
                "3. 不要急着教步骤或给结论——引导用户自己说出自动想法，就是本练习的目标；\n"
                "4. 每轮 60 字以内、1-2 个短段、口语化；单问号；不诊断。"
            ),
        ]
    )
    if not zh:
        system_prompt = (
            "You are guiding the user through the self-help exercise '" + exercise_name + "'. "
            "The user's expression is often vague (e.g. 'misunderstood, feeling awful'). Your job: "
            "(1) first empathically name the mixed feeling using their own words; "
            "(2) ask ONE clarifying question at a time to ground the vague feeling in something concrete "
            "(which sentence / which moment / where in the body); "
            "(3) do not rush to teach steps or give conclusions — helping the user voice their automatic "
            "thought IS the exercise; (4) each turn under 50 words, 1-2 short paragraphs, conversational, "
            "one question mark, no diagnosis."
        )

    try:
        # 引导是短轮次，系统 prompt 已承载完整契约（练习上下文+引导要求），
        # 不走主对话的长模板装配——保持轻量、独立于图状态。
        reply = _invoke(system_prompt, user_message, expected_language, mode="intervention")
        return _sanitize_output(reply, expected_language), "ok"
    except Exception:
        logger.exception("Exercise guidance generation failed; serving step guide echo.")
        fallback = (
            f"这一步慢慢来：{step_guide[:80]} 如果你愿意，说说刚才写下时心里冒出的第一个念头。"
            if zh
            else f"Take this step gently: {step_guide[:80]} If you like, tell me the first thought that came up as you wrote."
        )
        return fallback, "ok"
