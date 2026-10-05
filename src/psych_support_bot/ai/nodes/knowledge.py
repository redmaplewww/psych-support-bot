from psych_support_bot.ai.knowledge.index import detect_topics
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.ai.tools.knowledge_base import get_knowledge_context
from psych_support_bot.infra.telemetry.tracing import trace_span, update_span_output


def _profile_topics_and_beliefs(user_id: str) -> tuple[list[str], list]:
    """从画像活跃信念中提取话题 key 列表和 D3 信念（fail-open）。"""
    try:
        from psych_support_bot.infra.db.profile_repositories import list_active_beliefs
        from psych_support_bot.infra.db.session import SessionLocal

        with SessionLocal() as session:
            beliefs = list_active_beliefs(session, user_id, dimensions=("D1", "D3"), limit=20)
            topics = [b.key for b in beliefs if b.dimension == "D1" and b.confidence >= 0.4]
            d3_beliefs = [b for b in beliefs if b.dimension == "D3" and b.confidence >= 0.4]
            return topics, d3_beliefs
    except Exception:  # noqa: BLE001
        return [], []


def load_knowledge_context(state: GraphState) -> GraphState:
    with trace_span(
        "node.knowledge_loader",
        input={"user_message": state["user_message"], "mode": state["mode"]},
    ) as obs:
        # LLM 语义 topics（risk_classifier 阶段产出，闭集校验过）并进检索
        # 通道：词表外表达（"心情很低落"）由此可达。为空时与原行为一致。
        llm_topics = list(state.get("llm_topics") or [])
        topics = list(dict.fromkeys([*llm_topics, *detect_topics(state["user_message"])]))
        state["topics"] = topics
        # 通路5 + 第二层：画像信念话题 + D3 学派匹配注入知识检索。
        # 画像关闭时跳过——不注入任何画像信号。
        p_topics: list[str] = []
        d3_beliefs: list = []
        try:
            from psych_support_bot.infra.db.profile_repositories import is_profile_memory_enabled
            from psych_support_bot.infra.db.session import SessionLocal

            with SessionLocal() as _s:
                if is_profile_memory_enabled(_s, state["user_id"]):
                    p_topics, d3_beliefs = _profile_topics_and_beliefs(state["user_id"])
        except Exception:  # noqa: BLE001
            pass
        state["knowledge_context"] = get_knowledge_context(
            mode=state["mode"],
            risk_level=state["risk_result"].risk_level,
            user_message=state["user_message"],
            extra_topics=llm_topics,
            profile_topics=p_topics,
            profile_beliefs=d3_beliefs,
        )
        update_span_output(
            obs,
            {
                "topics": topics,
                "llm_topics": llm_topics,
                "knowledge_context_len": len(state["knowledge_context"]),
            },
        )
    return state
