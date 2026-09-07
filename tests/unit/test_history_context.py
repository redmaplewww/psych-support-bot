"""逐字近史（标准 API 格式）回归测试。

背景（Langfuse 2026-09-06 实证）：「换个方向吧，我感觉」被误读为继续
喝水话题——模型看不到逐字近史，摘要过期 + 粘贴截断。修复后近史以
标准 API 消息格式追加在末尾：[System, *history, Human(数据前缀+本轮)]。
"""

from typing import Any, ClassVar

import psych_support_bot.infra.llm.generation as gen


def _capture_messages(**overrides: Any):
    captured: dict = {}

    class _FakeResponse:
        content = "好的。"
        usage_metadata: ClassVar[dict] = {
            "input_tokens": 100,
            "output_tokens": 10,
            "total_tokens": 110,
        }

    class _FakeModel:
        def invoke(self, messages):
            captured["messages"] = messages
            return _FakeResponse()

    orig_model = gen.build_chat_model
    gen.build_chat_model = lambda **kwargs: _FakeModel()
    try:
        gen.generate_clinically_bounded_reply(
            user_message=overrides.pop("user_message", "换个方向吧，我感觉"),
            mode="support",
            risk_level="low",
            memory_summary=overrides.pop("memory_summary", "评估记录：PHQ-9 8分"),
            knowledge_context=overrides.pop("knowledge_context", ""),
            history=overrides.pop(
                "history",
                [
                    {"role": "user", "content": "我最近喝的水也很少"},
                    {"role": "assistant", "content": "喝水少这件事，其实和你现在的心情是连在一起的。"},
                    {"role": "user", "content": "嗯嗯"},
                    {"role": "assistant", "content": "你能觉察到，这本身就是一种自我关怀的开始。"},
                ],
            ),
            **overrides,
        )
    finally:
        gen.build_chat_model = orig_model
    return captured["messages"]


def test_history_rendered_as_standard_api_roles() -> None:
    """近史以 user/assistant 标准角色按序出现，位于 system 与本轮之间。"""
    messages = _capture_messages()
    assert type(messages[0]).__name__ == "SystemMessage"
    roles = [type(m).__name__ for m in messages[1:-1]]
    assert roles == ["HumanMessage", "AIMessage", "HumanMessage", "AIMessage"]
    # 本轮用户消息在最后一条
    assert type(messages[-1]).__name__ == "HumanMessage"
    assert "换个方向吧" in messages[-1].content


def test_history_contents_preserved_verbatim() -> None:
    """近史内容逐字保留——「换个方向」的可解读性依赖完整原文。"""
    messages = _capture_messages()
    bodies = [m.content for m in messages[1:-1]]
    assert "我最近喝的水也很少" in bodies
    assert any("自我关怀" in b for b in bodies)


def test_final_human_carries_context_prefix_then_user_message() -> None:
    """数据区前缀 + 本轮原话同在最后一条 HumanMessage。"""
    messages = _capture_messages(memory_summary="评估记录：PHQ-9 12分（中度）")
    final = messages[-1].content
    assert final.index("[User Memory") < final.index("换个方向吧")


def test_empty_history_yields_two_message_minimum() -> None:
    messages = _capture_messages(history=[])
    assert type(messages[0]).__name__ == "SystemMessage"
    assert len(messages) == 2


def test_shape_carries_reverse_anchor_and_forward_rule() -> None:
    """形态条款含反向锚点与前向动作守则；180 字上限已取消。"""
    from psych_support_bot.ai.prompts.templates import (
        build_mode_shape_prompt,
        build_output_contract_prompt,
    )

    shape = build_mode_shape_prompt("support", "low")
    assert "request for MORE, not less" in shape
    assert "Only clear acute distress earns the short pure-presence form" in shape
    assert "this reply must move forward" in shape

    contract = build_output_contract_prompt("zh")
    assert "under 180 words" not in contract
    assert "Never introduce or volunteer who you are unprompted" in contract
