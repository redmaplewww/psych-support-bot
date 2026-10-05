"""Provider-neutral account registration and rotating web sessions."""

from __future__ import annotations

import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from psych_support_bot.api.auth import (
    create_access_token,
    hash_password,
    token_secret_hash,
    verify_password,
)
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.models import AuthSession, User, UserCredential
from psych_support_bot.infra.db.repositories import record_usage_event

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,64}$")
_MIN_PASSWORD_LENGTH = 8
_INVALID_LOGIN = "Invalid username or password."
_INVALID_SESSION = "Invalid or expired session."


def _validate_credentials(username: str, password: str) -> None:
    if not _USERNAME_RE.fullmatch(username):
        raise HTTPException(
            status_code=422,
            detail="Username must be 3-64 chars of letters, digits, '_' or '-'.",
        )
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters.")


def _new_account_id() -> str:
    return f"acct_{uuid4().hex}"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _credential_for_username(session: Session, username: str) -> UserCredential | None:
    # Exact lookup preserves historical case-sensitive usernames. New accounts
    # are stored lowercase, so a second normalized lookup gives predictable UX.
    credential = session.query(UserCredential).filter(UserCredential.username == username).one_or_none()
    if credential is not None:
        return credential
    normalized = username.lower()
    return session.query(UserCredential).filter(UserCredential.username == normalized).one_or_none()


def _build_refresh_session(user_id: str) -> tuple[AuthSession, str, str]:
    settings = get_settings()
    session_id = uuid4().hex
    secret = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    record = AuthSession(
        id=session_id,
        user_id=user_id,
        refresh_token_hash=token_secret_hash(secret),
        csrf_token_hash=token_secret_hash(csrf),
        created_at=now,
        last_used_at=now,
        expires_at=now + timedelta(days=settings.auth_refresh_token_days),
    )
    return record, f"{session_id}.{secret}", csrf


def _auth_result(session: Session, user_id: str, token_version: int) -> dict[str, str | int]:
    refresh_session, refresh_token, csrf_token = _build_refresh_session(user_id)
    session.add(refresh_session)
    return {
        "user_id": user_id,
        "access_token": create_access_token(user_id, account_version=token_version),
        "token_type": "bearer",
        "expires_in": get_settings().auth_access_token_minutes * 60,
        "_refresh_token": refresh_token,
        "_csrf_token": csrf_token,
    }


def register_user(session: Session, username: str, password: str) -> dict[str, str | int]:
    _validate_credentials(username, password)
    normalized = username.lower()
    duplicate = session.query(UserCredential).filter(func.lower(UserCredential.username) == normalized).first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Username already taken.")

    user_id = _new_account_id()
    session.add(User(id=user_id, status="active", token_version=1))
    session.add(UserCredential(user_id=user_id, username=normalized, password_hash=hash_password(password)))
    session.flush()
    result = _auth_result(session, user_id, 1)
    record_usage_event(session, user_id, "auth_register")
    session.commit()
    return result


def authenticate_user(session: Session, username: str, password: str) -> dict[str, str | int]:
    credential = _credential_for_username(session, username)
    valid = credential is not None and verify_password(password, credential.password_hash)
    user = session.query(User).filter(User.id == credential.user_id).first() if credential is not None else None
    if not valid or user is None or user.status != "active":
        # Never create telemetry under an attacker-controlled username. A failed
        # attempt is associated only when a real internal account was resolved.
        if credential is not None:
            record_usage_event(session, credential.user_id, "auth_login_failed")
            session.commit()
        raise HTTPException(status_code=401, detail=_INVALID_LOGIN)

    result = _auth_result(session, user.id, user.token_version)
    record_usage_event(session, user.id, "auth_login")
    session.commit()
    return result


def authenticate_external_identity(session: Session, identity) -> dict[str, str | int]:
    """Resolve a cryptographically verified provider identity to an account."""
    from psych_support_bot.domain.auth.identity import find_identity, link_verified_identity

    linked = find_identity(session, identity)
    created = linked is None
    if linked is None:
        user = User(id=_new_account_id(), status="active", token_version=1)
        session.add(user)
        session.flush()
        linked = link_verified_identity(session, user.id, identity)
    else:
        user = session.query(User).filter(User.id == linked.user_id).first()
        if user is None or user.status != "active":
            raise HTTPException(status_code=401, detail=_INVALID_LOGIN)
        linked.last_used_at = datetime.now(UTC)

    result = _auth_result(session, user.id, user.token_version)
    record_usage_event(session, user.id, "auth_register" if created else "auth_login")
    session.commit()
    return result


def _parse_refresh_token(raw_token: str) -> tuple[str, str]:
    try:
        session_id, secret = raw_token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=_INVALID_SESSION) from exc
    if len(session_id) != 32 or len(secret) < 32:
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)
    return session_id, secret


def refresh_user_session(
    session: Session,
    raw_token: str,
    csrf_token: str = "",
    *,
    require_csrf: bool = True,
) -> dict[str, str | int]:
    session_id, secret = _parse_refresh_token(raw_token)
    current = session.query(AuthSession).filter(AuthSession.id == session_id).first()
    now = datetime.now(UTC)
    if (
        current is None
        or current.revoked_at is not None
        or _as_utc(current.expires_at) <= now
        or not hmac.compare_digest(current.refresh_token_hash, token_secret_hash(secret))
        or (
            require_csrf
            and (not csrf_token or not hmac.compare_digest(current.csrf_token_hash, token_secret_hash(csrf_token)))
        )
    ):
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)
    user = session.query(User).filter(User.id == current.user_id).first()
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)

    replacement, replacement_token, replacement_csrf = _build_refresh_session(user.id)
    updated = (
        session.query(AuthSession)
        .filter(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .update(
            {
                AuthSession.revoked_at: now,
                AuthSession.last_used_at: now,
                AuthSession.replaced_by_id: replacement.id,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        session.rollback()
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)
    session.add(replacement)
    session.commit()
    return {
        "user_id": user.id,
        "access_token": create_access_token(user.id, account_version=user.token_version),
        "token_type": "bearer",
        "expires_in": get_settings().auth_access_token_minutes * 60,
        "_refresh_token": replacement_token,
        "_csrf_token": replacement_csrf,
    }


def revoke_refresh_session(
    session: Session,
    raw_token: str,
    csrf_token: str = "",
    *,
    require_csrf: bool = True,
) -> None:
    """Revoke one valid browser session; malformed input remains an idempotent logout."""
    try:
        session_id, secret = _parse_refresh_token(raw_token)
    except HTTPException:
        return
    current = session.query(AuthSession).filter(AuthSession.id == session_id).first()
    if (
        current is None
        or not hmac.compare_digest(current.refresh_token_hash, token_secret_hash(secret))
        or (
            require_csrf
            and (not csrf_token or not hmac.compare_digest(current.csrf_token_hash, token_secret_hash(csrf_token)))
        )
    ):
        return
    if current.revoked_at is None:
        current.revoked_at = datetime.now(UTC)
        current.last_used_at = current.revoked_at
        session.commit()


def revoke_all_user_sessions(session: Session, user_id: str) -> int:
    now = datetime.now(UTC)
    count = (
        session.query(AuthSession)
        .filter(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .update({AuthSession.revoked_at: now}, synchronize_session=False)
    )
    user = session.query(User).filter(User.id == user_id).first()
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)
    user.token_version += 1
    session.commit()
    return int(count)
