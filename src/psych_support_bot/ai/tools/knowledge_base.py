"""知识检索门面（P3-B 统一后）：组装、预算、埋点。

检索与渲染在 ai/knowledge/index.py 同层；分场景实践指导语料在
ai/knowledge/practice_guidance.py——本模块只做拼装。
"""

from psych_support_bot.ai.knowledge.index import (
    detect_topics,
    render_knowledge_sections,
    retrieve_knowledge_entries,
)
from psych_support_bot.ai.knowledge.practice_guidance import KNOWLEDGE_SNIPPETS


def get_knowledge_context(
    mode: str, risk_level: str, user_message: str = "", extra_topics: list[str] | None = None
) -> str:
    # LLM 语义 topics（闭集）与关键词 topics 并集：词表外表达（"心情很低落"）
    # 由语义通道补齐。extra_topics 为空时行为与纯关键词通道完全一致。
    topics = list(dict.fromkeys([*detect_topics(user_message), *(extra_topics or [])]))
    entries = retrieve_knowledge_entries(user_message, mode, risk_level, limit=5, extra_topics=extra_topics)
    base_snippets = KNOWLEDGE_SNIPPETS.get(mode, [])

    sections = [
        f"Risk level: {risk_level}.",
        f"Mode: {mode}.",
        f"Practice guidance: {' '.join(base_snippets)}",
    ]
    if topics:
        sections.append(f"Detected topics: {', '.join(topics)}.")
    if entries:
        sections.extend(render_knowledge_sections(entries))
    if not entries:
        # 商业化指标：无知识命中（prompt 走结构化兜底框架）的触发率是
        # "是否值得投语义检索"的决策依据，走 Langfuse 事件不落库。
        _emit_fallback_metric(mode, topics)
    return _clip_context(" ".join(sections).strip())


# 知识区总量预算（字符）。命中条目多时截断尾部——检索已按相关性排序，
# 截尾损失最小。防外部语料一次命中多条（单条可达数百字符）挤占回复预算。
# 与 index._clip 语义不同：这是上下文级预算截断（保留原空白、省略号收尾），
# entry 级裁剪（空白归一）仍在检索层内部。
KNOWLEDGE_CONTEXT_BUDGET = 2400


def _clip_context(context: str, budget: int = KNOWLEDGE_CONTEXT_BUDGET) -> str:
    if len(context) <= budget:
        return context
    return context[: budget - 1].rstrip() + "…"


def _emit_fallback_metric(mode: str, topics: list[str]) -> None:
    try:
        from psych_support_bot.infra.telemetry.tracing import get_langfuse

        client = get_langfuse()
        if client is not None:
            client.create_event(name="knowledge_fallback", metadata={"mode": mode, "topics": topics})
    except Exception:  # noqa: BLE001 – 观测失败不影响对话主流程
        pass
