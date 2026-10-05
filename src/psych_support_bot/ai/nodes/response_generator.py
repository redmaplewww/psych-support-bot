import logging
import re

from langgraph.config import get_stream_writer

from psych_support_bot.ai.consultation import consultation_agent_descriptions
from psych_support_bot.ai.prompts.templates import build_crisis_safety_prompt
from psych_support_bot.ai.safety.crisis import build_crisis_reply
from psych_support_bot.ai.schemas.messages import GeneratedReply
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.infra.llm.generation import (
    generate_clinically_bounded_reply,
    generate_clinically_bounded_reply_stream_sync,
    generate_multidisciplinary_consultation,
)
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output

logger = logging.getLogger(__name__)

# Verbatim-repeat guard: when the freshly generated reply repeats the previous
# bot turn (Langfuse 2026-09-02 会话 c4fd09cc：第 3 轮投机输出与第 2 轮逐字相同),
# the speculative copy is discarded and generation retries once with an
# explicit anti-repeat instruction; if a repeat still survives, it is replaced
# by a grounding line instead of being delivered (追加式补救会让用户再次收到
# 整段复读). Template (critical) replies are exempt — they are intentionally
# fixed.
_ZH_REPEAT_FALLBACK = "我在这里。我们接着往下聊——此刻你的身体有什么感觉？是紧绷、发沉，还是别的感受？"
_EN_REPEAT_FALLBACK = (
    "I'm here with you. Let's keep going — what do you notice in your body right "
    "now: tension, heaviness, or something else?"
)

# Normalized-shape key for near-verbatim detection: LLM copies often differ
# only in whitespace, full/half-width punctuation, or quote style — normalize
# all of those away so "same words, different punctuation" still counts as a
# repeat (正则归一化修复), while genuinely reworded replies stay distinct.
_REPEAT_NOISE_RE = re.compile(r"[\s\u3000。，、；：！？（）【】《》“”‘’…·—\-–,.:;!?()\"'`~*]+")


def _repeat_shape(text: str) -> str:
    return _REPEAT_NOISE_RE.sub("", text or "").lower()


def _is_verbatim_repeat(reply_text: str, previous_reply: str) -> bool:
    previous = (previous_reply or "").strip()
    stripped = (reply_text or "").strip()
    if not previous or not stripped:
        return False
    return _repeat_shape(stripped) == _repeat_shape(previous)


def _anti_repeat_note() -> str:
    """投机/正式生成共用的防复读指令。

    引用上一轮回复而不摘录原文——摘录会给上下文再添一份范例拷贝，
    反而加重 2026-09-02 那类"照抄记忆区成品"的倾向。
    """
    return (
        "Anti-repeat guard: your previous reply is already contained in the "
        "memory context. Do NOT restate it or merely reword it — the user has "
        "moved on to a new message. Answer that message with entirely fresh "
        "content; if it lacks material for a full reply, ask one brief "
        "clarifying question instead of repeating old content."
    )


def _split_reply_messages(text: str) -> list[str]:
    """Split a generated reply into IM-style bubble messages (max 3).

    Returns an empty list when the reply cannot be meaningfully split,
    so callers fall back to rendering ``text`` as a single bubble.
    """
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if len(parts) <= 1:
        return []
    if len(parts) > 3:
        parts = [*parts[:2], "\n\n".join(parts[2:])]
    return parts


def _refusal_context_note(refusal_history: list[str]) -> str:
    """B3.3 refusal note. Empty string when nothing was refused.

    抽成纯函数：投机并行路径（risk_classifier）需要在不改 state 的前提下
    为投机回复复刻同样的注入语义。
    """
    if not refusal_history:
        return ""
    refused_topics = ", ".join(refusal_history)
    return (
        f"User has previously declined exercises related to: {refused_topics}. "
        "Do not recommend the same types of exercises again. "
        "Offer a different approach or explore why the previous suggestion did not fit."
    )


def _inject_refusal_context(state: GraphState) -> None:
    """B3.3: If user has refused exercises before, inject context into loop_hint.

    This tells the LLM not to repeat recommendations for topics the user
    has already declined, improving personalization.
    """
    refusal_note = _refusal_context_note(state.get("refusal_history", []))
    if not refusal_note:
        return
    existing_hint = state.get("loop_hint", "")
    state["loop_hint"] = refusal_note + " " + existing_hint if existing_hint else refusal_note


def _generate_normal_reply(state: GraphState, risk_level: str, no_question_mode: bool) -> str:
    """非投机路径的回复生成（critical 模板 / crisis LLM / 正常 LLM）。

    从 generate_response 抽出：投机回复被判为复读时也走这里重生成。
    """
    # Only critical risk uses pure template reply (imminent danger)
    # High risk now goes through LLM with crisis safety prompt injected
    if risk_level == "critical":
        reply_text = build_crisis_reply(
            state["risk_result"],
            user_message=state["user_message"],
            expected_language=state.get("expected_language", ""),
        )
        state["consultation_opinions"] = []
        return reply_text
    if state["mode"] == "crisis" and risk_level == "high":
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
                expected_language=state.get("expected_language", ""),
                emotional_state=state.get("emotional_state", ""),
                conversation_intent=state.get("conversation_intent", "new_request"),
                # Phase 3: 使用切片上下文或 recent_history
                history=[dict(turn) for turn in (state.get("slice_context") or state.get("recent_history") or [])],
            )
            state["consultation_opinions"] = []
        except Exception:  # noqa: BLE001 - crisis path must always return a safe response
            logger.warning("LLM generation failed for high-risk; using crisis template fallback")
            state["fallback_used"] = True
            reply_text = build_crisis_reply(
                state["risk_result"],
                user_message=state["user_message"],
                expected_language=state.get("expected_language", ""),
            )
        return reply_text
    try:
        if state.get("consultation_required", False):
            # 会诊路径：agent fan-out 不流式，最终综合在 stream_tokens 开启时
            # 经 on_token 逐块推出（intervention 轮的流式覆盖）。
            writer = get_stream_writer() if state.get("stream_tokens") else None
            on_token = (lambda t: writer({"type": "token", "text": t})) if writer else None
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
                expected_language=state.get("expected_language", ""),
                no_question_mode=no_question_mode,
                emotional_state=state.get("emotional_state", ""),
                conversation_intent=state.get("conversation_intent", "new_request"),
                on_token=on_token,
                history=[dict(turn) for turn in (state.get("slice_context") or state.get("recent_history") or [])],
            )
            state["consultation_opinions"] = opinions
        else:
            # Phase 3: 如果是新切片，在 loop_hint 中添加边界提示
            base_loop_hint = state.get("loop_hint", "Start broad, reflect, then narrow.")
            slice_metadata = state.get("slice_metadata", {})
            if slice_metadata.get("is_new_slice"):
                boundary_reason = slice_metadata.get("boundary_reason", "")
                if boundary_reason == "explicit_switch":
                    slice_hint = "Note: User just switched topics explicitly. Focus on the new topic and don't reference the previous conversation unless the user asks."
                elif boundary_reason == "sleep_boundary":
                    slice_hint = "Note: This is a new conversation after sleep. Start fresh; treat it as a new session."
                elif boundary_reason.startswith("time_gap"):
                    slice_hint = "Note: User returned after a time gap. Acknowledge continuity if needed, but focus on the current message."
                else:
                    slice_hint = ""

                loop_hint_with_slice = f"{slice_hint}\n\n{base_loop_hint}" if slice_hint else base_loop_hint
            else:
                loop_hint_with_slice = base_loop_hint

            # 第一层接出：D6 表达偏好→语气指令注入 turn context。
            try:
                from psych_support_bot.ai.profile.renderer import d6_tone_directives
                from psych_support_bot.infra.db.session import SessionLocal

                with SessionLocal() as _s:
                    d6_hint = d6_tone_directives(_s, state["user_id"], state.get("expected_language", ""))
                if d6_hint:
                    loop_hint_with_slice = f"{loop_hint_with_slice}\n\nTone guidance: {d6_hint}"
            except Exception:  # noqa: BLE001
                pass

            # 第二层接出：D2 严重度→干预强度（不告诉用户，只调整内部行为）。
            try:
                from psych_support_bot.infra.db.profile_repositories import list_active_beliefs
                from psych_support_bot.infra.db.session import SessionLocal as _SL

                with _SL() as _s:
                    d2_beliefs = list_active_beliefs(_s, state["user_id"], dimensions=("D2",), limit=5)
                high_severity = any(
                    b.confidence >= 0.7
                    and any(k in (b.key or "") for k in ("severity.", "checkin_mood", "checkin_anxiety"))
                    for b in d2_beliefs
                )
                if high_severity:
                    loop_hint_with_slice = (
                        "The user's recent profile suggests elevated distress. "
                        "Prioritize validation, support, and gentle pacing. "
                        "Avoid direct challenges or pushing for action steps.\n\n" + loop_hint_with_slice
                    )
            except Exception:  # noqa: BLE001
                pass

            # 好奇心注入：不确定时自然提问 + 新话题探索 + 行为信号异常时主动关心。
            try:
                from psych_support_bot.ai.profile.behavioral import detect_behavioral_signals
                from psych_support_bot.ai.profile.renderer import curiosity_signal_with_topics
                from psych_support_bot.infra.db.session import SessionLocal as _CS

                current_topics = list(state.get("topics") or [])
                with _CS() as _s:
                    cue = curiosity_signal_with_topics(
                        _s, state["user_id"], current_topics, state.get("expected_language", "")
                    )

                # 行为信号检测（从 graph state 中提取时间戳）。
                bot_ts = state.get("bot_message_timestamp")
                user_ts = state.get("user_message_timestamp")
                recent_lens = state.get("recent_message_lengths") or []
                session_hour = user_ts.hour if user_ts and hasattr(user_ts, "hour") else -1

                # 读取用户的睡眠窗口（来自 UserTimeProfile 打卡行为推断）。
                sleep_win = None
                try:
                    from psych_support_bot.infra.db.repositories import get_user_time_profile

                    with _CS() as _s:
                        tp = get_user_time_profile(_s, state["user_id"])
                    if tp and tp.sleep_start_hour is not None and tp.sleep_end_hour is not None:
                        sleep_win = (tp.sleep_start_hour, tp.sleep_end_hour)
                except Exception:  # noqa: BLE001
                    pass

                beh_signals = detect_behavioral_signals(
                    user_message=state["user_message"],
                    bot_message_timestamp=bot_ts,
                    user_message_timestamp=user_ts,
                    recent_message_lengths=recent_lens,
                    session_hour=session_hour,
                    vad_metadata=state.get("vad_metadata") or None,
                    sleep_window=sleep_win,
                )

                is_en = state.get("expected_language", "") != "zh"
                hint_parts: list[str] = []
                if cue:
                    hint_parts.append(cue)
                for sig in beh_signals:
                    hint_parts.append(sig.get("question_en" if is_en else "question_zh", ""))
                if hint_parts:
                    loop_hint_with_slice = f"{loop_hint_with_slice}\n\nCuriosity: {' '.join(hint_parts)}"
            except Exception:  # noqa: BLE001
                pass

            # 工作单元 E：从画像快照读取支持策略，注入 turn context。
            # profile_policy_enabled=False 时跳过（影子模式/紧急回退）。
            try:
                from psych_support_bot.infra.config.settings import get_settings as _gs

                if _gs().profile_policy_enabled:
                    from psych_support_bot.infra.db.models import ProfileSnapshot
                    from psych_support_bot.infra.db.profile_repositories import is_profile_memory_enabled
                    from psych_support_bot.infra.db.session import SessionLocal as _PS

                    snapshot = None
                    with _PS() as _s:
                        # 用户暂停或清除画像后，不读取快照。
                        if is_profile_memory_enabled(_s, state["user_id"]):
                            snapshot = (
                                _s.query(ProfileSnapshot)
                                .filter(
                                    ProfileSnapshot.user_id == state["user_id"],
                                    ProfileSnapshot.status.in_(("active", "shadow")),
                                )
                                .order_by(ProfileSnapshot.version.desc())
                                .limit(1)
                                .first()
                            )
                    if snapshot and snapshot.support_policy_json:
                        import json as _json

                        policy = _json.loads(snapshot.support_policy_json)
                        policy_parts = []
                        if policy.get("response_length") == "brief":
                            policy_parts.append("Keep responses brief and direct.")
                        if policy.get("pacing") == "validate_before_suggestions":
                            policy_parts.append("Validate the user's experience before offering suggestions.")
                        paths = policy.get("preferred_knowledge_paths", [])
                        if paths:
                            policy_parts.append(f"Prefer knowledge from: {', '.join(paths)}.")
                        if policy_parts:
                            loop_hint_with_slice = f"{loop_hint_with_slice}\n\nSupport policy: {' '.join(policy_parts)}"
            except Exception:  # noqa: BLE001
                pass

            gen_kwargs = {
                "user_message": state["user_message"],
                "mode": state["mode"],
                "risk_level": state["risk_result"].risk_level,
                "memory_summary": state.get("memory_summary", ""),
                "knowledge_context": state.get("knowledge_context", ""),
                "consultation_required": False,
                "consultation_agents": [],
                "consultation_framework": "",
                "interview_stage": state.get("interview_stage", "engagement"),
                "question_strategy": state.get("question_strategy", "open"),
                "challenge_allowed": bool(state.get("challenge_allowed", False)),
                "loop_hint": loop_hint_with_slice,  # Phase 3: 注入切片提示
                "expected_language": state.get("expected_language", ""),
                "no_question_mode": no_question_mode,
                # 复读事故（Langfuse 2026-09-02 c4fd09cc）的第二道防线：
                # 生成时就明确告知上一轮已交付过内容，不要复述。
                "anti_repeat_note": _anti_repeat_note(),
                "emotional_state": state.get("emotional_state", ""),
                "conversation_intent": state.get("conversation_intent", "new_request"),
                # Phase 3: 优先使用切片上下文（更完整的话题边界）
                "history": [dict(turn) for turn in (state.get("slice_context") or state.get("recent_history") or [])],
            }
            if state.get("stream_tokens"):
                # 句子级流式：普通 LLM 路径逐块经 get_stream_writer 推 token，
                # 供 /respond/stream SSE 消费喂 TTS。同时累积完整文本写入
                # reply_text——下游 safety_reviewer/持久化与非流式路径完全一致。
                writer = get_stream_writer()
                pieces: list[str] = []
                for chunk in generate_clinically_bounded_reply_stream_sync(**gen_kwargs):
                    pieces.append(chunk)
                    writer({"type": "token", "text": chunk})
                reply_text = "".join(pieces)
            else:
                reply_text = generate_clinically_bounded_reply(**gen_kwargs)
            state["consultation_opinions"] = []
    except Exception:  # noqa: BLE001 - response path must always return a safe response
        logger.warning("LLM generation failed; using template fallback")
        state["fallback_used"] = True
        is_zh = state.get("expected_language", "") == "zh" or (
            not state.get("expected_language")
            and any("\u4e00" <= char <= "\u9fff" for char in state.get("user_message", ""))
        )
        if is_zh:
            reply_text = (
                "我在这里陪你。虽然我现在遇到了一些技术困难，但我仍然想支持你。我们可以先慢下来，聊一聊你现在的感受。"
            )
        else:
            reply_text = (
                "I am here with you. Although I am experiencing some technical difficulty, "
                "I still want to support you. Let us slow down and talk about how you are feeling right now."
            )
    return reply_text


def generate_response(state: GraphState) -> GraphState:
    with trace_span(
        "node.response_generator",
        input={
            "user_message": state["user_message"],
            "mode": state["mode"],
            "risk_level": state["risk_result"].risk_level,
            "consultation_required": state.get("consultation_required", False),
        },
    ) as gen_obs:
        state["fallback_used"] = False
        risk_level = state["risk_result"].risk_level
        # Quiet mode is honored on ordinary support paths only; the crisis
        # paths below deliberately ignore it so safety resources still flow.
        no_question_mode = bool(state.get("no_question_mode", False))

        # B3.3: Inject refusal history into loop_hint before LLM generation
        _inject_refusal_context(state)

        # M2 首答延迟优化：规则判 low/elevated 的 support 消息已在 risk_classifier
        # 与风险 LLM 分类并行投机生成回复；最终裁决未升级（仍 ≤ elevated）时
        # 直接采用，跳过本次 LLM 调用（2 次串行 → 1 次往返）。
        # 复读防线：投机 prompt 看不到上一轮回复原文，模型可能照抄记忆区里的
        # 成品（Langfuse 2026-09-02 实测）——重复的投机回复一律丢弃，回退到
        # 正常生成路径（其自带防复读指令与重复重试）。
        speculative = state.get("speculative_reply")
        if (
            speculative
            and risk_level in {"low", "elevated"}
            and state["mode"] == "support"
            and not _is_verbatim_repeat(speculative, state.get("last_bot_reply", ""))
        ):
            reply_text = speculative
            state["consultation_opinions"] = []
            state["speculative_reply"] = None
            update_span_output(gen_obs, {"speculative_reply_used": True})
            if state.get("stream_tokens"):
                # 投机快路径的流式语义：整文虽已备好，仍按 token 经 writer 推出，
                # 让 respond_stream 照常切句——live 字幕/句级朗读与普通路径同管道。
                # 缺这一步时大多数普通轮走投机，前端只能等 final 整段蹦出（2026-09-10 实证）。
                get_stream_writer()({"type": "token", "text": reply_text})
        else:
            if speculative:
                # 丢弃重复投机回复；日志留痕供 Langfuse 巡检对照。
                logger.warning("Speculative reply repeated the previous turn; discarding and regenerating.")
                update_span_output(gen_obs, {"speculative_reply_discarded_as_repeat": True})
            state["speculative_reply"] = None
            reply_text = _generate_normal_reply(state, risk_level, no_question_mode)

        # 复读最终防线（Langfuse 2026-09-02 会话 c4fd09cc）：无论来自投机、会诊
        # 还是危机 LLM 路径，与上一轮逐字（含标点/空白归一化后）相同的回复一律
        # 替换为落地句——追加差异化句会让用户再次收到整段复读原文。
        # Template (critical) replies are intentionally fixed.
        if risk_level != "critical" and _is_verbatim_repeat(reply_text, state.get("last_bot_reply", "")):
            logger.warning("Reply repeats previous turn verbatim; replacing with grounding line.")
            reply_text = _ZH_REPEAT_FALLBACK if state.get("expected_language", "") != "en" else _EN_REPEAT_FALLBACK

        state["generated_reply"] = GeneratedReply(
            text=reply_text,
            style=state["mode"],
            # Crisis replies keep hotline resources in one intact bubble.
            messages=_split_reply_messages(reply_text) if risk_level in {"low", "elevated"} else [],
        )
        state["consultation_notes"] = (
            f"{len(state.get('consultation_opinions', []))} agents consulted; stage={state.get('interview_stage', 'engagement')}; question={state.get('question_strategy', 'open')}"
            if state.get("consultation_required")
            else f"single response path; stage={state.get('interview_stage', 'engagement')}; question={state.get('question_strategy', 'open')}"
        )
        update_span_output(
            gen_obs,
            {
                "reply_text": reply_text[:200],
                "fallback_used": state["fallback_used"],
                "consultation_notes": state["consultation_notes"],
            },
        )
    return state
