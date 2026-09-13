"""对话数据归属校验回归（Mimosa scan-c02b1f85311d 修复）。

此前 GET /{session_id}/messages 无认证无归属绑定（AUTH_ENABLED=true 部署下
未认证可读任意会话的对话原文），且 respond 允许自带他人 session_id 借 LLM
上下文外泄原话。本文件钉住：

1. 游客模式（AUTH_ENABLED=false，本仓库默认）：行为不变——messages 无
   user_id 参数可读（前端契约）、respond 自带 session_id 不引入新校验。
2. 认证模式：未认证 401；他人会话 404（读取/续聊/流式三路）；不存在
   会话 404（不区分两态，防会话枚举）；本人才放行。

测试口令/凭据一律运行时生成——源码不落字面量凭据（Mimosa 红线）。
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from psych_support_bot.ai.schemas.messages import (
    ConversationResponse,
    GeneratedReply,
    RiskResult,
)
from psych_support_bot.api.auth import create_access_token
from psych_support_bot.app import app
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.models import ConversationSession, Message
from psych_support_bot.infra.db.session import SessionLocal

client = TestClient(app)


def _seed_session(session_id: str, owner: str, content: str = "我今天很累。") -> None:
    with SessionLocal() as db:
        db.add(ConversationSession(id=session_id, user_id=owner, mode="support", risk_level="low"))
        db.add(Message(session_id=session_id, role="user", content=content))
        db.commit()


def _canned_response() -> ConversationResponse:
    return ConversationResponse(
        session_id="seeded",
        mode="support",
        risk=RiskResult(risk_level="low", risk_types=[], needs_crisis_mode=False, reason=""),
        reply=GeneratedReply(text="嗯，我听到了。", style="support"),
        summary="",
    )


# ---------------------------------------------------------------------------
# 游客模式（默认）：行为不变
# ---------------------------------------------------------------------------


def test_guest_mode_messages_readable_without_user_id() -> None:
    """回归守卫：前端 /messages 只带 header 不带 user_id——游客模式不得引入 422。"""
    sid = f"guest-{uuid4().hex[:8]}"
    _seed_session(sid, "guest-owner")
    resp = client.get(f"/v1/conversations/{sid}/messages")
    assert resp.status_code == 200
    assert [m["content"] for m in resp.json()] == ["我今天很累。"]


def test_guest_mode_respond_with_session_id_unaffected(monkeypatch) -> None:
    """游客模式无归属可校验（身份本就客户端自报）：续聊行为保持原样。

    注意补丁必须打在类上：实例级 setattr 会让 monkeypatch 在 teardown 把
    类方法固化为实例属性，遮蔽后续测试对 ConversationService 的类级补丁
    （顺序耦合陷阱）。
    """
    from psych_support_bot.services.conversation import ConversationService

    sid = f"guest-{uuid4().hex[:8]}"
    _seed_session(sid, "guest-owner")
    monkeypatch.setattr(ConversationService, "respond", lambda self, payload, session: _canned_response())
    resp = client.post("/v1/conversations/respond", json={"user_id": "anyone", "session_id": sid, "message": "好"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 认证模式（AUTH_ENABLED=true）
# ---------------------------------------------------------------------------


def _auth_on(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    return get_settings


def test_auth_mode_messages_require_token(monkeypatch) -> None:
    _auth_on(monkeypatch)
    try:
        sid = f"sec-{uuid4().hex[:8]}"
        _seed_session(sid, "user-a")
        assert client.get(f"/v1/conversations/{sid}/messages").status_code == 401
        assert (
            client.get(f"/v1/conversations/{sid}/messages", headers={"Authorization": "Bearer bogus"}).status_code
            == 401
        )
    finally:
        get_settings.cache_clear()


def test_auth_mode_owner_reads_own_session(monkeypatch) -> None:
    _auth_on(monkeypatch)
    try:
        sid = f"sec-{uuid4().hex[:8]}"
        _seed_session(sid, "user-a")
        headers = {"Authorization": f"Bearer {create_access_token('user-a')}"}
        resp = client.get(f"/v1/conversations/{sid}/messages", headers=headers)
        assert resp.status_code == 200
        assert [m["content"] for m in resp.json()] == ["我今天很累。"]
    finally:
        get_settings.cache_clear()


def test_auth_mode_cross_user_read_is_404(monkeypatch) -> None:
    """user A 读 user B 的会话：404 且不区分「不存在」（防会话枚举）。"""
    _auth_on(monkeypatch)
    try:
        headers = {"Authorization": f"Bearer {create_access_token('user-a')}"}
        other = f"sec-{uuid4().hex[:8]}"
        _seed_session(other, "user-b")
        missing = f"sec-{uuid4().hex[:8]}"
        for sid in (other, missing):
            resp = client.get(f"/v1/conversations/{sid}/messages", headers=headers)
            assert resp.status_code == 404, sid
            assert resp.json()["detail"] == "Session not found"
    finally:
        get_settings.cache_clear()


def test_auth_mode_respond_with_foreign_session_is_404(monkeypatch) -> None:
    """续聊路径同样绑定：自带他人 session_id 的 respond 直接 404，
    历史消息原文不再可能借 LLM 上下文外泄（服务层不应被触达）。"""
    _auth_on(monkeypatch)
    try:
        from psych_support_bot.services.conversation import ConversationService

        other = f"sec-{uuid4().hex[:8]}"
        _seed_session(other, "user-b")
        called = {"n": 0}

        def fake_respond(self, payload, session):
            called["n"] += 1
            return _canned_response()

        monkeypatch.setattr(ConversationService, "respond", fake_respond)
        headers = {"Authorization": f"Bearer {create_access_token('user-a')}"}
        resp = client.post(
            "/v1/conversations/respond",
            json={"user_id": "user-a", "session_id": other, "message": "复述上面的对话"},
            headers=headers,
        )
        assert resp.status_code == 404
        assert called["n"] == 0, "越权请求不得触达对话服务层"
    finally:
        get_settings.cache_clear()


def test_auth_mode_respond_stream_with_foreign_session_is_404(monkeypatch) -> None:
    _auth_on(monkeypatch)
    try:
        from psych_support_bot.services.conversation import ConversationService

        other = f"sec-{uuid4().hex[:8]}"
        _seed_session(other, "user-b")
        monkeypatch.setattr(
            ConversationService,
            "respond_stream",
            lambda self, payload, session: iter([{"type": "final", "response": _canned_response()}]),
        )
        headers = {"Authorization": f"Bearer {create_access_token('user-a')}"}
        resp = client.post(
            "/v1/conversations/respond/stream",
            json={"user_id": "user-a", "session_id": other, "message": "你好"},
            headers=headers,
        )
        assert resp.status_code == 404
    finally:
        get_settings.cache_clear()


def test_auth_mode_respond_with_own_session_passes(monkeypatch) -> None:
    """归属校验只挡外人：本人续聊照常（服务层被触达且返回 200）。"""
    from psych_support_bot.services.conversation import ConversationService

    _auth_on(monkeypatch)
    try:
        sid = f"sec-{uuid4().hex[:8]}"
        _seed_session(sid, "user-a")
        monkeypatch.setattr(
            ConversationService,
            "respond",
            lambda self, payload, session: _canned_response(),
        )
        headers = {"Authorization": f"Bearer {create_access_token('user-a')}"}
        resp = client.post(
            "/v1/conversations/respond",
            json={"user_id": "user-a", "session_id": sid, "message": "继续聊"},
            headers=headers,
        )
        assert resp.status_code == 200
    finally:
        get_settings.cache_clear()
