"""LLM 故障降级回归测试（Langfuse 巡检 2026-08-23/28 的发现）。

上游 LLM 不可用（限流 / 内容安全拦截 / 网络故障）时，任何用户路径都不应
收到 500 或空响应：
1. 问卷进行中 → 经 _invoke 咽喉层回退确定性结构化提示（不走 LLM）。
2. 图整体失败 → 回落静态安全文案。

降级下沉到 _invoke 之后，这里的测试直接替换 build_chat_model 返回必抛
异常的假模型，覆盖真实全链路（_invoke 重试/降级 → 调用方 fallback）。
"""

from types import SimpleNamespace
from uuid import uuid4

import openai

from psych_support_bot.ai.schemas.messages import ConversationRequest
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.infra.llm import generation as llm_generation
from psych_support_bot.services import conversation as conversation_module
from psych_support_bot.services.conversation import conversation_service

init_db()


class _AlwaysFailingModel:
    """invoke 必抛给定异常的假 ChatOpenAI，用于打穿 _invoke 全链路。"""

    def __init__(self, exc_factory) -> None:
        self._exc_factory = exc_factory

    def invoke(self, _messages: object) -> object:
        raise self._exc_factory()


def _patch_failing_model(monkeypatch, exc_factory) -> None:
    monkeypatch.setattr(
        llm_generation,
        "build_chat_model",
        lambda **_: _AlwaysFailingModel(exc_factory),
    )


def test_questionnaire_llm_failure_falls_back_to_deterministic_prompt(monkeypatch) -> None:
    """问卷进行中 LLM 调用失败（403 内容安全拦截）时，返回确定性题干提示。"""
    _patch_failing_model(
        monkeypatch,
        lambda: openai.PermissionDeniedError(
            "Request rejected by content safety review",
            response=SimpleNamespace(status_code=403),
            body=None,
        ),
    )

    user_id = f"llm-fail-questionnaire-{uuid4()}"
    with SessionLocal() as session:
        start = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我想做 PHQ-9"),
            session=session,
        )
        assert start.mode == "assessment"
        assert start.debug["source"] == "assessment_start"

        # 此时 LLM 全链路必抛 403——答题仍应得到确定性提示
        step = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="2"),
            session=session,
        )

    assert step.mode == "assessment"
    assert step.reply.text
    # 确定性降级文本包含进度前缀与题干，而非错误堆栈或空响应
    assert "第 2/9 题" in step.reply.text or "Question 2/9" in step.reply.text
    assert "做事时提不起兴趣" in step.reply.text or "0" in step.reply.text


def test_graph_failure_serves_static_fallback(monkeypatch) -> None:
    """conversation graph 整体失败时，用户收到静态安全文案而非 500。"""

    class _BrokenGraph:
        def invoke(self, *_: object, **__: object) -> dict:
            raise RuntimeError("simulated graph failure")

    monkeypatch.setattr(conversation_module, "conversation_graph", _BrokenGraph())

    user_id = f"llm-fail-graph-{uuid4()}"
    with SessionLocal() as session:
        result = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我最近很累"),
            session=session,
        )

    assert result.debug.get("fallback_used") is True
    assert result.reply.text
    # 静态兜底文案必须包含安全指引（中文用户 → 中文文案 + 120）
    assert "120" in result.reply.text
    assert "技术" in result.reply.text


def test_permission_denied_error_type_is_caught(monkeypatch) -> None:
    """真实的 openai.PermissionDeniedError 必须被 _invoke 降级路径捕获。"""
    _patch_failing_model(
        monkeypatch,
        lambda: openai.PermissionDeniedError(
            "Request rejected by Alibaba Cloud content safety review",
            response=SimpleNamespace(status_code=403),
            body=None,
        ),
    )

    user_id = f"llm-fail-403-{uuid4()}"
    with SessionLocal() as session:
        start = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="我想做 GAD-7"),
            session=session,
        )
        step = conversation_service.respond(
            ConversationRequest(user_id=user_id, message="1"),
            session=session,
        )

    assert start.mode == "assessment"
    assert step.reply.text
    assert not step.reply.text.startswith("Traceback")
