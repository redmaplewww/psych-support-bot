from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output


def load_memory_context(state: GraphState) -> GraphState:
    """memory_summary 在图启动前已由 build_memory_snapshot 组装完成
    （services/conversation.py），本节点只做观测，不再改写。

    历史说明：旧的 "Previous turn: {session_summary}" 拼接分支是死代码——
    session_summary 由图末尾的 summary_writer 才产出，执行到这里恒为空；
    若未来真有值，双份注入反而会制造重复上下文（Langfuse 2026-09 复读
    事故的病灶之一），故直接移除。
    """
    with trace_span(
        "node.memory_loader",
        input={"memory_summary_len": len(state.get("memory_summary") or "")},
    ) as obs:
        update_span_output(obs, {"memory_summary": (state.get("memory_summary") or "")[:200]})
    return state
