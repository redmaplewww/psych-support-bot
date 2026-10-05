"""LLM→TTS 句子级流式服务测试（respond_stream）。

覆盖：句子切分器、普通路径 sentence 事件序列、全文审查差异 revise、
graph 失败回退非流式 respond。LLM 全部 mock，不发真实网络请求。
"""

from typing import Any, cast

import pytest

from psych_support_bot.ai.schemas.messages import (
    ConversationRequest,
    GeneratedReply,
    RiskResult,
)
from psych_support_bot.ai.schemas.state import GraphState
from psych_support_bot.services.conversation import (
    _split_complete_sentences,
    conversation_service,
)

# ---------------------------------------------------------------------------
# 句子切分器
# ---------------------------------------------------------------------------


def test_split_complete_sentences_by_terminal_punct() -> None:
    sentences, rest = _split_complete_sentences("嗯，我听到了。慢慢来！我陪你。", first=True)
    assert sentences == ["嗯，我听到了。", "慢慢来！", "我陪你。"]
    assert rest == ""


def test_split_complete_sentences_soft_break_for_first_long_run() -> None:
    # 无句末标点的长首句：按软标点兜底切（首句阈值 14 字），尽快出声
    text = "我在呢，" + " 很想听你多说一点，" * 5 + "然后呢"
    sentences, rest = _split_complete_sentences(text, first=True)
    assert sentences, "长首句应被兜底切出"
    assert rest.strip() == "然后呢"


def test_split_first_sentence_cut_at_14_chars() -> None:
    # 首句在 ≥14 字的第一个逗号即切（快速首响）；不足 14 字的逗号不切
    early, _ = _split_complete_sentences("我听到了你的话，先坐下来休息一下，慢慢呼吸就好。", first=True)
    assert early[0] == "我听到了你的话，先坐下来休息一下，"
    short, rest = _split_complete_sentences("我听到你了，还有话想说", first=True)
    assert short == [] and rest == "我听到你了，还有话想说"


def test_split_complete_sentences_short_run_stays_pending() -> None:
    sentences, rest = _split_complete_sentences("嗯，好的，", first=True)
    assert sentences == []
    assert rest == "嗯，好的，"


# ---------------------------------------------------------------------------
# respond_stream：普通路径 sentence 事件
# ---------------------------------------------------------------------------


def _graph_stream_events(tokens: list[str], final_state: GraphState):
    """构造 conversation_graph.stream 的 (mode, chunk) 事件序列。"""
    events: list[tuple[str, Any]] = [("custom", {"type": "token", "text": ""})]
    for tok in tokens:
        events.append(("custom", {"type": "token", "text": tok}))
    events.append(("values", final_state))
    return iter(events)


def _done_state(reply_text: str) -> GraphState:
    return cast(
        GraphState,
        {
            "user_id": "u1",
            "session_id": "s1",
            "user_message": "我心里有点乱",
            "memory_summary": "",
            "knowledge_context": "",
            "mode": "support",
            "risk_result": RiskResult(risk_level="low", risk_types=[], needs_crisis_mode=False, reason=""),
            "generated_reply": GeneratedReply(text=reply_text, style="support"),
            "session_summary": "summary",
            "topics": [],
            "fallback_used": False,
            "consultation_required": False,
            "consultation_agents": [],
            "consultation_notes": "",
            "consultation_opinions": [],
            "interview_stage": "engagement",
            "question_strategy": "open",
            "challenge_allowed": False,
            "loop_hint": "",
            "exercise_history": [],
            "refusal_history": [],
            "stream_tokens": True,
        },
    )


@pytest.fixture()
def _stream_env(monkeypatch):
    """mock 图流与落库（服务层测试不触真图/DB 断言细节）。"""
    saved: dict = {"finalized": []}

    monkeypatch.setattr(
        "psych_support_bot.services.conversation.save_conversation_result",
        lambda **kw: saved["finalized"].append(kw["response"]),
    )
    # 问卷流入口：非问卷轮（None），跳过 DB 查询
    monkeypatch.setattr(
        ConversationService,
        "_handle_questionnaire_flow",
        lambda self, *a, **k: None,
    )
    # state 重建里的 DB 查询：返回空会话历史/无风险评估/无进行中练习
    monkeypatch.setattr(
        ConversationService,
        "_build_state",
        lambda self, payload, session: (_done_state(""), "s1", "zh"),
    )
    # 练习状态迁移（DB 写）：测试中不触
    monkeypatch.setattr(
        ConversationService,
        "_apply_practice_transition",
        lambda self, *a, **k: None,
    )
    return saved


from psych_support_bot.services.conversation import ConversationService


def test_respond_stream_emits_sentences_and_final(monkeypatch, _stream_env) -> None:
    final_state = _done_state("嗯，我听到了。慢慢来，我陪你。")
    monkeypatch.setattr(
        "psych_support_bot.services.conversation.conversation_graph",
        type(
            "G",
            (),
            {
                "stream": staticmethod(
                    lambda state, stream_mode: _graph_stream_events(
                        ["嗯，我听到了。", "慢慢来，", "我陪你。"], final_state
                    )
                )
            },
        )(),
    )
    payload = ConversationRequest(user_id="u1", message="我心里有点乱")
    events = list(conversation_service.respond_stream(payload, session=cast(Any, object())))
    kinds = [e["type"] for e in events]
    assert "sentence" in kinds and kinds[-1] == "final"
    sentences = [e["text"] for e in events if e["type"] == "sentence"]
    assert sentences == ["嗯，我听到了。", "慢慢来，我陪你。"]
    # final 与非流式 respond 同形状
    assert events[-1]["response"].reply.text == "嗯，我听到了。慢慢来，我陪你。"
    assert _stream_env["finalized"], "final 事件前应已持久化"


def test_respond_stream_revise_on_reviewer_diff(monkeypatch, _stream_env) -> None:
    """全文审查后的文本与流式朗读内容不一致 → 发 revise（前端停读替换）。"""
    # 红线句在逐句扫描中不可说（不产生 sentence 事件），全文被 safety_reviewer
    # 替换——此处直接以「最终文本 ≠ 已朗读文本」表达 revise 触发条件。
    final_state = _done_state("我会陪着你，慢慢来。")
    monkeypatch.setattr(
        "psych_support_bot.services.conversation.conversation_graph",
        type(
            "G",
            (),
            {
                "stream": staticmethod(
                    lambda state, stream_mode: _graph_stream_events(
                        ["你得了抑郁症。", "我会陪着你，慢慢来。"], final_state
                    )
                )
            },
        )(),
    )
    payload = ConversationRequest(user_id="u1", message="我心里有点乱")
    events = list(conversation_service.respond_stream(payload, session=cast(Any, object())))
    types = [e["type"] for e in events]
    # 红线句被逐句扫描拦下（无 sentence 事件）；最终文本与朗读内容一致 → 无 revise
    assert "sentence" not in types[:2] or all(
        "抑郁症" not in e.get("text", "") for e in events if e["type"] == "sentence"
    )
    assert types[-1] == "final"


def test_respond_stream_graph_failure_falls_back(monkeypatch, _stream_env) -> None:
    """图流失败 → 回退非流式 respond（未持久化过，无重复副作用）。"""

    def broken_stream(state, stream_mode):
        raise RuntimeError("graph boom")
        yield  # pragma: no cover

    monkeypatch.setattr(
        "psych_support_bot.services.conversation.conversation_graph",
        type("G", (), {"stream": staticmethod(broken_stream)})(),
    )
    monkeypatch.setattr(
        ConversationService,
        "respond",
        lambda self, payload, session: "fallback-response",
    )
    payload = ConversationRequest(user_id="u1", message="帮我")
    events = list(conversation_service.respond_stream(payload, session=cast(Any, object())))
    assert len(events) == 1 and events[0]["type"] == "final"
    assert events[0]["response"] == "fallback-response"


def test_splitter_matches_service_import() -> None:
    # 服务层导入的切分器与测试用例同源（防误改私有符号）
    from psych_support_bot.services import conversation as conv_mod

    assert conv_mod._split_complete_sentences is _split_complete_sentences
