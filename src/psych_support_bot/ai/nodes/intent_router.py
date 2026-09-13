from psych_support_bot.ai.practice_flow import detect_practice_intent
from psych_support_bot.ai.routers.intent import REFUSAL_KEYWORDS, detect_mode
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.ai.utils.text_matching import _contains_keyword, _normalize_text
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output


def _route_practice(state: GraphState) -> str:
    """练习流路由裁决：返回 practice_route（"" = 本轮与练习无关）。

    危机优先由外层保证——mode=crisis 时本函数不会被调用。练习意图
    分类是纯规则（零 LLM），见 practice_flow.detect_practice_intent。
    """
    intent = detect_practice_intent(
        state["user_message"],
        active_practice=state.get("active_practice"),
        risk_result=state["risk_result"],
        last_bot_reply=str(state.get("last_bot_reply", "")),
    )
    return "" if intent.kind == "none" else intent.kind


def route_intent(state: GraphState) -> GraphState:
    with trace_span(
        "node.intent_router",
        input={"user_message": state["user_message"], "current_mode": state.get("mode", "")},
    ) as obs:
        if state.get("mode") == "crisis":
            # 危机短路：mode 已由 risk_classifier 改写，任何练习/会话状态
            # 都不劫持本轮（进行中的练习由服务层在图结束后暂停）。
            update_span_output(obs, {"mode": "crisis", "skipped": True})
            return state

        # 图内引导练习分支：用户在练习中 / 请求练习 / 确认开始。
        # 命中即接管本轮（mode=intervention），不再走关键词 detect_mode——
        # 步骤回答（「窗外的树、桌子」）不是 support/intervention 话题，
        # 必须回到练习状态机而不是被当成普通倾诉。
        practice_route = _route_practice(state)
        if practice_route:
            state["practice_route"] = practice_route
            state["mode"] = "intervention"
            update_span_output(
                obs,
                {"mode": state["mode"], "practice_route": practice_route},
            )
            return state

        # B3.2: Before re-routing, check if user is refusing an exercise.
        # If so, record the refused topic in refusal_history.
        normalized, compact = _normalize_text(state["user_message"])
        has_refusal = any(_contains_keyword(normalized, compact, kw) for kw in REFUSAL_KEYWORDS)
        if has_refusal and state.get("mode") == "intervention":
            refusal_history = state.get("refusal_history", [])
            # Record the topics that were active when the refusal occurred
            current_topics = state.get("topics", [])
            for topic in current_topics:
                if topic not in refusal_history:
                    refusal_history.append(topic)
            state["refusal_history"] = refusal_history

        state["mode"] = detect_mode(state["user_message"])
        update_span_output(
            obs,
            {
                "mode": state["mode"],
                "refusal_detected": has_refusal,
                "refusal_history": state.get("refusal_history", []),
            },
        )
    return state
