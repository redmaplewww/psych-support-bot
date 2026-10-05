"""Authentication primitives: password hashing, short access tokens, guards.

设计要点：
- 密码哈希用标准库 pbkdf2_hmac（SHA-256，600k 迭代），零新增重依赖；
  存储格式 ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``。
- JWT access tokens use the immutable internal account ID as ``sub`` and carry
  explicit issuer, audience, token type, JWT ID, and account token version.
- require_auth 守卫挂在各数据路由上：AUTH_ENABLED=false 时是 no-op
  （面板登录 UI 尚未上线，本地开发与既有测试不携带 token），
  true 时无/坏 token 一律 401。user_id 的权威来源是 JWT sub——
  路由不得再接受请求参数里的 user_id 自报值做越权数据访问。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from psych_support_bot.api.user_id import normalize_user_id
from psych_support_bot.infra.config.settings import get_settings

PBKDF2_ITERATIONS = 600_000
_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_secret_hash(secret: str) -> str:
    """Hash a high-entropy token secret for lookup-safe persistence."""
    return hashlib.sha256(secret.encode()).hexdigest()


def create_access_token(user_id: str, *, account_version: int | None = None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iss": settings.auth_token_issuer,
        "aud": settings.auth_token_audience,
        "typ": "access",
        "jti": uuid4().hex,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=settings.auth_access_token_minutes),
        "account_version": account_version if account_version is not None else _account_version(user_id),
    }
    return pyjwt.encode(payload, settings.jwt_secret_key, algorithm=_ALGORITHM)


def _account_version(user_id: str) -> int | None:
    from psych_support_bot.infra.db.models import User
    from psych_support_bot.infra.db.session import SessionLocal

    with SessionLocal() as session:
        # Filtered query (not primary-key get): the row is only ever read back
        # when its own id column equals the requested subject.
        user = session.query(User).filter(User.id == user_id).first()
        return user.token_version if user and user.status == "active" else None


def _decode_access_payload(token: str) -> dict:
    settings = get_settings()
    try:
        payload = pyjwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[_ALGORITHM],
            audience=settings.auth_token_audience,
            issuer=settings.auth_token_issuer,
            options={"require": ["sub", "iss", "aud", "typ", "jti", "iat", "nbf", "exp"]},
        )
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
    if payload.get("typ") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


def validate_account_token(token: str) -> str:
    payload = _decode_access_payload(token)
    user_id = normalize_user_id(str(payload["sub"]), field="token subject")
    version = _account_version(user_id)
    if not version or payload.get("account_version") != version:
        raise HTTPException(status_code=401, detail="Account credentials are no longer valid; please sign in again.")
    return user_id


def decode_access_token(token: str) -> str:
    """校验并返回 sub（user_id）；任何失败统一以 401 呈现，不泄漏原因细节。"""
    return normalize_user_id(str(_decode_access_payload(token)["sub"]), field="token subject")


# auto_error=False 让缺失头也走我们的统一 401 JSON，而非 FastAPI 默认 403。
_bearer_scheme = HTTPBearer(auto_error=False)


def require_account_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """Always require a valid account token for account-management endpoints."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return validate_account_token(credentials.credentials)


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """数据路由守卫：AUTH_ENABLED=false 时 no-op 返回空串；true 时返回 JWT sub。

    返回值即当前认证用户——后续各端点把数据查询的 user_id 收敛到这个值，
    即完成"认证 → 归属校验"的闭环（防止伪造埋点归属/跨用户读取）。
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return ""
    return require_account_auth(credentials)


def request_user_id(request: Request, declared: str | None = None) -> str:
    """数据端点 user_id 的权威判定（认证 → 归属校验闭环）。

    - AUTH_ENABLED=true：token sub 权威。declared（query/body 自报值）
      缺失时直接用 sub；不一致返回 403（身份有效但无权访问他人数据），
      防"登录用户 A 读写用户 B"的越权与伪造埋点归属。
    - AUTH_ENABLED=false（游客直进模式）：declared 必填（缺失 422，
      与原 Query 校验语义一致）并透传，本地开发/既有测试不受影响。

    用法：GET 端点 ``user_id = request_user_id(request)``；
    body 型端点 ``user_id = request_user_id(request, payload.user_id)``。
    """
    settings = get_settings()
    if not settings.auth_enabled:
        if not declared:
            raise HTTPException(status_code=422, detail="user_id is required.")
        return normalize_user_id(declared)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    current = validate_account_token(auth_header.removeprefix("Bearer ").strip())
    if declared and declared != current:
        raise HTTPException(status_code=403, detail="User ID does not match the authenticated identity.")
    return current
