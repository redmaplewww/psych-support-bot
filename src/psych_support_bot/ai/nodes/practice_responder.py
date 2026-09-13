"""图内练习轮响应节点（对话式 54321）。

到达本节点的前提（intent_router 已裁决）：mode != crisis 且
practice_route 非空。危机轮在 risk_classifier/intent_router 阶段就已
改道主链危机路径，永远不进这里；elevated 在此软着陆暂停（「宁暂停
不漏接」，exercise_ai 同源语义——但用户主动请求练习的 offer/consent
轮不受 elevated 拦截：练习本身就是用户要的稳定化工具）。

落库不在本节点：图内不持 DB 会话，practice_action 交给服务层在图
结束后执行（与 save_conversation_result 同层的持久化约定）。
"""

from psych_support_bot.ai.practice_flow import (
    PRACTICE_TAG,
    build_elevated_pause_reply,
    build_generated_reply,
    build_offer_reply,
    build_pause_reply,
    build_resume_reply,
    build_start_reply,
    exercise_steps,
    generate_completion_reply,
    generate_step_reply,
)
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output


def _as_step(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def practice_responder(state: GraphState) -> GraphState:
    route = str(state.get("practice_route") or "")
    practice = state.get("active_practice") or {}
    lang = state.get("expected_language", "")
    risk = state["risk_result"]

    with trace_span(
        "node.practice_responder",
        input={
            "route": route,
            "practice_step": practice.get("step"),
            "risk_level": risk.risk_level,
        },
    ) as obs:
        state["practice_action"] = ""

        # elevated 软着陆：仅在练习进行中拦截（用户正在作答时说出痛苦）；
        # offer/consent/resume/restart 是用户主动请求，练习即稳定化工具，
        # 不被 elevated 拦下（high/critical 已在更早阶段进危机路径）。
        if risk.risk_level == "elevated" and route in {"continue", "pause"}:
            reply = build_elevated_pause_reply(lang)
            state["practice_action"] = "pause"
            build_generated_reply(state, reply)
            update_span_output(obs, {"practice_action": "pause", "reason": "elevated"})
            return state

        if route == "offer":
            text, _chips = build_offer_reply(lang)
            state["practice_action"] = "offer"
        elif route == "consent":
            text = build_start_reply(lang)
            state["practice_action"] = "start"
        elif route == "resume":
            text = build_resume_reply(lang, _as_step(practice.get("step")))
            state["practice_action"] = "resume"
        elif route == "restart":
            text = build_start_reply(lang)
            state["practice_action"] = "restart"
        elif route == "pause":
            text = build_pause_reply(lang, _as_step(practice.get("step")))
            state["practice_action"] = "pause"
        elif route == "continue":
            step = _as_step(practice.get("step"))
            _, steps = exercise_steps(lang)
            if steps and step >= len(steps) - 1:
                # 第 5 步已回答 → 收尾轮（服务层随后落 ExerciseRecord）。
                text = generate_completion_reply(state, expected_language=lang)
                state["practice_action"] = "complete"
            else:
                text = generate_step_reply(
                    state,
                    step_index=step,
                    user_reply=state["user_message"],
                    expected_language=lang,
                )
                state["practice_action"] = "advance"
        else:  # 防御：未知 route 不说话，交回主链（不应发生）
            update_span_output(obs, {"practice_action": "", "reason": "unknown_route"})
            return state

        build_generated_reply(state, text)
        update_span_output(
            obs,
            {
                "practice_action": state["practice_action"],
                "reply_text": text[:160],
                "tag": PRACTICE_TAG,
            },
        )
    return state
