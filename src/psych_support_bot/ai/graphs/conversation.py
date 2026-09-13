from langgraph.graph import END, START, StateGraph

from psych_support_bot.ai.nodes.consultation_planner import plan_consultation
from psych_support_bot.ai.nodes.intent_router import route_intent
from psych_support_bot.ai.nodes.knowledge import load_knowledge_context
from psych_support_bot.ai.nodes.memory import load_memory_context
from psych_support_bot.ai.nodes.practice_responder import practice_responder
from psych_support_bot.ai.nodes.response_generator import generate_response
from psych_support_bot.ai.nodes.risk_classifier import classify_risk
from psych_support_bot.ai.nodes.safety_reviewer import review_response
from psych_support_bot.ai.nodes.summary_writer import write_summary
from psych_support_bot.ai.schemas.state import GraphState


def _route_after_risk(state: GraphState) -> str:
    return "crisis" if state["risk_result"].needs_crisis_mode else "normal"


def _route_after_intent(state: GraphState) -> str:
    # 练习分支：intent_router 裁决为本轮走图内引导练习（crisis 已在其
    # 内部短路，永远不会带 practice_route 进入危机路径）。
    return "practice" if state.get("practice_route") else "normal"


def build_conversation_graph():
    graph = StateGraph(GraphState)
    graph.add_node("risk_classifier", classify_risk)
    graph.add_node("intent_router", route_intent)
    graph.add_node("practice_responder", practice_responder)
    graph.add_node("consultation_planner", plan_consultation)
    graph.add_node("memory_loader", load_memory_context)
    graph.add_node("knowledge_loader", load_knowledge_context)
    graph.add_node("response_generator", generate_response)
    graph.add_node("safety_reviewer", review_response)
    graph.add_node("summary_writer", write_summary)

    graph.add_edge(START, "risk_classifier")
    graph.add_conditional_edges(
        "risk_classifier",
        _route_after_risk,
        {
            "crisis": "intent_router",
            "normal": "intent_router",
        },
    )
    graph.add_conditional_edges(
        "intent_router",
        _route_after_intent,
        {
            "practice": "practice_responder",
            "normal": "consultation_planner",
        },
    )
    # 练习分支跳过 consultation/memory/knowledge/response_generator——
    # 步骤引导自包含（练习内容 + transcript），不需要知识检索；风险筛查
    # 与红线审查照常：practice_responder → safety_reviewer → summary_writer。
    graph.add_edge("practice_responder", "safety_reviewer")
    graph.add_edge("consultation_planner", "memory_loader")
    graph.add_edge("memory_loader", "knowledge_loader")
    graph.add_edge("knowledge_loader", "response_generator")
    graph.add_edge("response_generator", "safety_reviewer")
    graph.add_edge("safety_reviewer", "summary_writer")
    graph.add_edge("summary_writer", END)
    return graph.compile()


conversation_graph = build_conversation_graph()
