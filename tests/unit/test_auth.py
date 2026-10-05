"""JWT 认证（api/auth.py + domain/auth/service.py + 守卫挂载）单测。

覆盖：
1. 密码哈希/校验（pbkdf2 格式、错误密码、损坏存储串）。
2. token 签发/校验（sub 往返、坏 token、过期 token）。
3. 注册/登录服务（成功、重名、弱密码、错误密码）。
4. AUTH_ENABLED 两种模式下的守卫行为（端到端，TestClient）。

测试口令一律运行时生成——源码不落任何字面量凭据（Mimosa 红线）。
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from psych_support_bot.api.auth import (
    PBKDF2_ITERATIONS,
    create_access_token,
    decode_access_token,
    hash_password,
    validate_account_token,
    verify_password,
)
from psych_support_bot.app import app
from psych_support_bot.domain.auth.identity import VerifiedIdentity, find_identity, link_verified_identity
from psych_support_bot.domain.auth.service import authenticate_user, register_user
from psych_support_bot.infra.config.settings import Settings, get_settings
from psych_support_bot.infra.db.models import AuthSession, User, UserCredential
from psych_support_bot.infra.db.session import SessionLocal

client = TestClient(app)


def _rand_username() -> str:
    return f"t{uuid4().hex[:10]}"


def _rand_password(length: int = 12) -> str:
    return f"pw-{uuid4().hex[: length - 3]}"


# --- 密码哈希 ---


def test_password_hash_roundtrip() -> None:
    password = _rand_password()
    stored = hash_password(password)
    assert stored.startswith(f"pbkdf2_sha256${PBKDF2_ITERATIONS}$")
    assert verify_password(password, stored)
    assert not verify_password(_rand_password(), stored)


def test_password_hash_salts_unique() -> None:
    assert hash_password(_rand_password()) != hash_password(_rand_password())


def test_verify_password_rejects_malformed_storage() -> None:
    assert not verify_password("x", "not-a-valid-format")
    assert not verify_password("x", "bcrypt$1$ab$cd")


# --- token 签发/校验 ---


def test_token_roundtrip() -> None:
    token = create_access_token("user-42")
    assert decode_access_token(token) == "user-42"


def test_token_rejects_garbage() -> None:
    with pytest.raises(HTTPException) as exc:
        decode_access_token("not.a.jwt")
    assert exc.value.status_code == 401


def test_token_rejects_expired() -> None:
    settings = get_settings()
    expired = pyjwt.encode(
        {"sub": "u", "iat": datetime.now(UTC) - timedelta(days=8), "exp": datetime.now(UTC) - timedelta(days=1)},
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        decode_access_token(expired)
    assert exc.value.status_code == 401


def test_token_rejects_wrong_secret() -> None:
    forged = pyjwt.encode(
        {"sub": "u", "exp": datetime.now(UTC) + timedelta(days=1)},
        "other-secret" * 4,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException):
        decode_access_token(forged)


# --- 注册/登录服务 ---


def test_register_and_login_flow() -> None:
    username = _rand_username()
    password = _rand_password()
    with SessionLocal() as session:
        result = register_user(session, username, password)
    account_id = str(result["user_id"])
    assert account_id.startswith("acct_")
    assert account_id != username
    assert decode_access_token(str(result["access_token"])) == account_id

    with SessionLocal() as session:
        login = authenticate_user(session, username, password)
    assert login["user_id"] == account_id


def test_legacy_username_account_keeps_existing_user_id() -> None:
    username = _rand_username()
    password = _rand_password()
    with SessionLocal() as session:
        session.add(User(id=username))
        session.add(UserCredential(user_id=username, username=username, password_hash=hash_password(password)))
        session.commit()
        result = authenticate_user(session, username, password)
    assert result["user_id"] == username


def test_register_rejects_duplicate() -> None:
    username = _rand_username()
    password = _rand_password()
    with SessionLocal() as session:
        register_user(session, username, password)
        with pytest.raises(HTTPException) as exc:
            register_user(session, username, password)
        assert exc.value.status_code == 409


def test_register_rejects_weak_password() -> None:
    with SessionLocal() as session, pytest.raises(HTTPException):
        register_user(session, _rand_username(), "short")


def test_login_rejects_wrong_password() -> None:
    username = _rand_username()
    with SessionLocal() as session:
        register_user(session, username, _rand_password())
    with SessionLocal() as session, pytest.raises(HTTPException) as exc:
        authenticate_user(session, username, _rand_password())
    assert exc.value.status_code == 401


# --- 守卫（AUTH_ENABLED 开/关） ---


def test_auth_disabled_keeps_data_routes_open(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    get_settings.cache_clear()
    try:
        resp = client.get("/v1/checkins", params={"user_id": "guard-off-user"})
        assert resp.status_code == 200
    finally:
        get_settings.cache_clear()


def _register_via_api() -> str:
    username = _rand_username()
    resp = client.post("/v1/auth/register", json={"username": username, "password": _rand_password()})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def test_auth_enabled_blocks_missing_and_bad_tokens(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    try:
        assert client.get("/v1/checkins", params={"user_id": "x"}).status_code == 401
        assert (
            client.get("/v1/checkins", params={"user_id": "x"}, headers={"Authorization": "Bearer bogus"}).status_code
            == 401
        )
        token = _register_via_api()
        # 有效 token + 他人 user_id：403（绑定校验，详见 test_auth_enabled_binds_user_id_to_token_sub）
        resp = client.get("/v1/checkins", params={"user_id": "x"}, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
    finally:
        get_settings.cache_clear()


def test_auth_enabled_binds_user_id_to_token_sub(monkeypatch) -> None:
    """授权闭环：认证开时数据归属只认 token sub——自报他人 user_id 一律 403，
    自报 sub 本人才放行。这也是埋点归属可信的前提。"""
    import time

    get_settings.cache_clear()
    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    try:
        username = f"bt{int(time.time())}"
        registered = client.post("/v1/auth/register", json={"username": username, "password": _rand_password()}).json()
        token = registered["access_token"]
        account_id = registered["user_id"]
        headers = {"Authorization": f"Bearer {token}"}
        # 本人数据：放行（空历史 200）
        assert client.get("/v1/checkins", params={"user_id": account_id}, headers=headers).status_code == 200
        # 他人 user_id：403（区别于 401——身份有效但越权）
        resp = client.get("/v1/checkins", params={"user_id": "victim-user"}, headers=headers)
        assert resp.status_code == 403
        # 省略 user_id（开模式可直接以 sub 查询）：放行
        assert client.get("/v1/checkins", headers=headers).status_code == 200
    finally:
        get_settings.cache_clear()


def test_auth_routes_always_open() -> None:
    """注册/登录端点本身不要求 token（AUTH_ENABLED=true 时仍可访问）。"""
    get_settings.cache_clear()
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("AUTH_ENABLED", "true")
        get_settings.cache_clear()
        resp = client.post("/v1/auth/register", json={"username": _rand_username(), "password": _rand_password()})
        assert resp.status_code == 200
    get_settings.cache_clear()


def test_refresh_cookie_rotates_and_replay_is_rejected() -> None:
    client.cookies.clear()
    response = client.post(
        "/v1/auth/register",
        json={"username": _rand_username(), "password": _rand_password()},
    )
    assert response.status_code == 200
    assert "HttpOnly" in response.headers.get("set-cookie", "")
    assert "refresh_token" not in response.json()
    old_refresh = client.cookies.get("psb_refresh")
    old_csrf = client.cookies.get("psb_csrf")
    assert old_refresh and old_csrf

    refreshed = client.post("/v1/auth/refresh", headers={"X-CSRF-Token": old_csrf})
    assert refreshed.status_code == 200
    assert refreshed.json()["user_id"] == response.json()["user_id"]
    assert client.cookies.get("psb_refresh") != old_refresh

    client.cookies.set("psb_refresh", old_refresh, path="/v1/auth")
    client.cookies.set("psb_csrf", old_csrf, path="/v1/auth")
    replay = client.post("/v1/auth/refresh", headers={"X-CSRF-Token": old_csrf})
    assert replay.status_code == 401


def test_logout_revokes_refresh_session_and_clears_cookie() -> None:
    client.cookies.clear()
    response = client.post(
        "/v1/auth/register",
        json={"username": _rand_username(), "password": _rand_password()},
    )
    csrf = client.cookies.get("psb_csrf")
    assert response.status_code == 200 and csrf
    logout = client.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 204
    assert client.cookies.get("psb_refresh") is None
    with SessionLocal() as session:
        assert session.query(AuthSession).filter(AuthSession.user_id == response.json()["user_id"]).one().revoked_at


def test_native_session_returns_rotating_refresh_token_without_cookie() -> None:
    client.cookies.clear()
    registered = client.post(
        "/v1/auth/register",
        json={
            "username": _rand_username(),
            "password": _rand_password(),
            "session_transport": "native",
        },
    )
    assert registered.status_code == 200
    first_refresh = registered.json()["refresh_token"]
    assert first_refresh
    assert client.cookies.get("psb_refresh") is None

    refreshed = client.post("/v1/auth/native/refresh", json={"refresh_token": first_refresh})
    assert refreshed.status_code == 200
    second_refresh = refreshed.json()["refresh_token"]
    assert second_refresh != first_refresh
    assert client.post("/v1/auth/native/refresh", json={"refresh_token": first_refresh}).status_code == 401
    assert client.post("/v1/auth/native/logout", json={"refresh_token": second_refresh}).status_code == 204
    assert client.post("/v1/auth/native/refresh", json={"refresh_token": second_refresh}).status_code == 401


def test_logout_all_revokes_sessions_and_current_access_token() -> None:
    registered = client.post(
        "/v1/auth/register",
        json={
            "username": _rand_username(),
            "password": _rand_password(),
            "session_transport": "native",
        },
    ).json()
    response = client.post(
        "/v1/auth/logout-all",
        headers={"Authorization": "Bearer " + registered["access_token"]},
    )
    assert response.status_code == 200
    assert response.json()["revoked_sessions"] == 1
    with pytest.raises(HTTPException):
        validate_account_token(registered["access_token"])
    assert (
        client.post(
            "/v1/auth/native/refresh",
            json={"refresh_token": registered["refresh_token"]},
        ).status_code
        == 401
    )


def test_verified_identity_links_by_provider_issuer_and_subject() -> None:
    first = _rand_username()
    second = _rand_username()
    with SessionLocal() as session:
        first_id = str(register_user(session, first, _rand_password())["user_id"])
        second_id = str(register_user(session, second, _rand_password())["user_id"])
        identity = VerifiedIdentity(provider="google", issuer="https://accounts.google.com", subject="provider-user-1")
        linked = link_verified_identity(session, first_id, identity)
        session.commit()
        assert linked.subject_hash != identity.subject
        assert find_identity(session, identity).user_id == first_id
        with pytest.raises(HTTPException) as exc:
            link_verified_identity(session, second_id, identity)
        assert exc.value.status_code == 409


def test_oidc_provider_stays_disabled_without_client_ids(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("AUTH_GOOGLE_CLIENT_IDS", "")
    get_settings.cache_clear()
    try:
        response = client.post("/v1/auth/oidc/challenge", json={"provider": "google"})
        assert response.status_code == 503
    finally:
        get_settings.cache_clear()


def test_production_auth_requires_independent_stable_keys(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", "jwt-key-" * 8)
    monkeypatch.setenv("AUTH_IDENTITY_HASH_KEY", "")
    with pytest.raises(ValidationError, match="AUTH_IDENTITY_HASH_KEY"):
        Settings(_env_file=None)

    monkeypatch.setenv("AUTH_IDENTITY_HASH_KEY", "jwt-key-" * 8)
    with pytest.raises(ValidationError, match="must be independent"):
        Settings(_env_file=None)


def test_auth_capabilities_only_advertise_fully_configured_methods(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_GOOGLE_CLIENT_IDS", "web-client,ios-client")
    monkeypatch.setenv("AUTH_APPLE_CLIENT_IDS", "")
    monkeypatch.setenv("AUTH_HUAWEI_CLIENT_IDS", "huawei-client")
    monkeypatch.setenv("AUTH_PASSKEY_RP_ID", "example.com")
    monkeypatch.setenv("AUTH_PASSKEY_ORIGINS", "https://auth.example.com")
    get_settings.cache_clear()
    try:
        result = client.get("/v1/auth/capabilities")
        assert result.status_code == 200
        assert result.json() == {
            "password": True,
            "oidc_providers": ["google", "huawei"],
            "passkey": True,
        }
    finally:
        get_settings.cache_clear()


def test_oidc_exchange_uses_nonce_once_and_reuses_internal_account(monkeypatch) -> None:
    from psych_support_bot.domain.auth import oidc

    client.cookies.clear()
    get_settings.cache_clear()
    monkeypatch.setenv("AUTH_GOOGLE_CLIENT_IDS", "web-client,ios-client")
    get_settings.cache_clear()
    active_nonce = {"value": ""}

    class FakeJwksClient:
        def __init__(self, url: str):
            assert url == "https://www.googleapis.com/oauth2/v3/certs"

        def get_signing_key_from_jwt(self, token: str):
            assert len(token) >= 32
            return SimpleNamespace(key="verified-public-key")

    def fake_decode(*_args, **kwargs):
        assert "web-client" in kwargs["audience"]
        assert "https://accounts.google.com" in kwargs["issuer"]
        return {
            "iss": "https://accounts.google.com",
            "sub": "stable-provider-subject",
            "aud": "web-client",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            "nonce": active_nonce["value"],
        }

    monkeypatch.setattr(oidc.pyjwt, "PyJWKClient", FakeJwksClient)
    monkeypatch.setattr(oidc.pyjwt, "decode", fake_decode)
    oidc._jwks_client.cache_clear()
    try:
        challenge = client.post("/v1/auth/oidc/challenge", json={"provider": "google"}).json()
        active_nonce["value"] = challenge["nonce"]
        body = {
            "provider": "google",
            "challenge_id": challenge["challenge_id"],
            "nonce": challenge["nonce"],
            "id_token": "signed-id-token-placeholder-value-1234567890",
        }
        first = client.post("/v1/auth/oidc/exchange", json=body)
        assert first.status_code == 200
        account_id = first.json()["user_id"]
        assert account_id.startswith("acct_")
        assert client.post("/v1/auth/oidc/exchange", json=body).status_code == 401

        challenge = client.post("/v1/auth/oidc/challenge", json={"provider": "google"}).json()
        active_nonce["value"] = challenge["nonce"]
        body.update(challenge_id=challenge["challenge_id"], nonce=challenge["nonce"])
        second = client.post("/v1/auth/oidc/exchange", json=body)
        assert second.status_code == 200
        assert second.json()["user_id"] == account_id
    finally:
        oidc._jwks_client.cache_clear()
        get_settings.cache_clear()


def test_jwks_client_refuses_origins_outside_provider_allowlist():
    """出站 JWKS 拉取只允许 https + 固定供应商主机（SSRF 纵深防御）。"""
    from psych_support_bot.domain.auth import oidc

    for bad in (
        "http://www.googleapis.com/oauth2/v3/certs",  # 明文协议
        "https://localhost/oauth2/v3/certs",
        "https://127.0.0.1/oauth2/v3/certs",
        "https://169.254.169.254/latest/meta-data",  # 云元数据
        "https://10.0.0.8/certs",  # 私有段
        "https://www.googleapis.com.evil.example/oauth2/v3/certs",  # 后缀伪装
        "file:///etc/passwd",
    ):
        with pytest.raises(ValueError):
            oidc._jwks_client(bad)
    oidc._jwks_client.cache_clear()
