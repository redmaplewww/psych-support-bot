"""JWT 认证（api/auth.py + domain/auth/service.py + 守卫挂载）单测。

覆盖：
1. 密码哈希/校验（pbkdf2 格式、错误密码、损坏存储串）。
2. token 签发/校验（sub 往返、坏 token、过期 token）。
3. 注册/登录服务（成功、重名、弱密码、错误密码）。
4. AUTH_ENABLED 两种模式下的守卫行为（端到端，TestClient）。

测试口令一律运行时生成——源码不落任何字面量凭据（Mimosa 红线）。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from psych_support_bot.api.auth import (
    PBKDF2_ITERATIONS,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from psych_support_bot.app import app
from psych_support_bot.domain.auth.service import authenticate_user, register_user
from psych_support_bot.infra.config.settings import get_settings
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
    forged = pyjwt.encode({"sub": "u", "exp": datetime.now(UTC) + timedelta(days=1)}, "other-secret", algorithm="HS256")
    with pytest.raises(HTTPException):
        decode_access_token(forged)


# --- 注册/登录服务 ---


def test_register_and_login_flow() -> None:
    username = _rand_username()
    password = _rand_password()
    with SessionLocal() as session:
        result = register_user(session, username, password)
    assert result["user_id"] == username
    assert decode_access_token(result["access_token"]) == username

    with SessionLocal() as session:
        login = authenticate_user(session, username, password)
    assert login["user_id"] == username


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
        token = client.post("/v1/auth/register", json={"username": username, "password": _rand_password()}).json()[
            "access_token"
        ]
        headers = {"Authorization": f"Bearer {token}"}
        # 本人数据：放行（空历史 200）
        assert client.get("/v1/checkins", params={"user_id": username}, headers=headers).status_code == 200
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
