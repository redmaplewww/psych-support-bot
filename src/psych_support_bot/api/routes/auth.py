"""Account endpoints: register, login, rotating refresh, and logout.

Registration, login, refresh, and single-session logout are intentionally
reachable without an access token. Each operation validates its own credential.
"""

import hmac
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from psych_support_bot.api.auth import require_account_auth
from psych_support_bot.domain.auth.oidc import issue_oidc_challenge, verify_oidc_identity
from psych_support_bot.domain.auth.service import (
    authenticate_external_identity,
    authenticate_user,
    refresh_user_session,
    register_user,
    revoke_all_user_sessions,
    revoke_refresh_session,
)
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.session import get_db_session

router = APIRouter(prefix="/v1/auth", tags=["auth"])
REFRESH_COOKIE_NAME = "psb_refresh"
CSRF_COOKIE_NAME = "psb_csrf"


class AuthRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    session_transport: Literal["cookie", "native"] = "cookie"


class AuthResponse(BaseModel):
    user_id: str
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str | None = None


class OidcChallengeRequest(BaseModel):
    provider: str = Field(..., pattern="^(google|apple|huawei)$")


class OidcChallengeResponse(BaseModel):
    challenge_id: str
    nonce: str
    expires_in: int


class OidcExchangeRequest(BaseModel):
    provider: str = Field(..., pattern="^(google|apple|huawei)$")
    challenge_id: str = Field(..., min_length=32, max_length=64)
    nonce: str = Field(..., min_length=16, max_length=256)
    id_token: str = Field(..., min_length=32, max_length=16_384)
    session_transport: Literal["cookie", "native"] = "cookie"


class NativeRefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=64, max_length=512)


@router.get("/capabilities")
def auth_capabilities() -> dict[str, object]:
    settings = get_settings()
    providers = [
        name
        for name, client_ids in (
            ("google", settings.auth_google_client_ids),
            ("apple", settings.auth_apple_client_ids),
            ("huawei", settings.auth_huawei_client_ids),
        )
        if client_ids.strip()
    ]
    origins = [origin.strip() for origin in settings.auth_passkey_origins.split(",") if origin.strip()]
    return {
        "password": True,
        "oidc_providers": providers,
        "passkey": bool(settings.auth_passkey_rp_id and origins),
    }


def _set_session_cookies(response: Response, result: dict[str, str | int]) -> None:
    settings = get_settings()
    secure = settings.environment.lower() not in {"development", "test"}
    max_age = settings.auth_refresh_token_days * 24 * 60 * 60
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        str(result["_refresh_token"]),
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/v1/auth",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        str(result["_csrf_token"]),
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/v1/auth",
    )


def _deliver_session(
    response: Response,
    result: dict[str, str | int],
    transport: Literal["cookie", "native"],
) -> AuthResponse:
    if transport == "cookie":
        _set_session_cookies(response, result)
        return AuthResponse(**result)
    return AuthResponse(**result, refresh_token=str(result["_refresh_token"]))


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/v1/auth")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/v1/auth")


def _browser_session_credentials(request: Request, csrf_header: str) -> tuple[str, str]:
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME, "")
    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME, "")
    if not refresh_token or not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    return refresh_token, csrf_header


@router.post("/register", response_model=AuthResponse, response_model_exclude_none=True)
def register(
    payload: AuthRequest,
    response: Response,
    session: Session = Depends(get_db_session),
) -> AuthResponse:
    result = register_user(session, payload.username, payload.password)
    return _deliver_session(response, result, payload.session_transport)


@router.post("/login", response_model=AuthResponse, response_model_exclude_none=True)
def login(
    payload: AuthRequest,
    response: Response,
    session: Session = Depends(get_db_session),
) -> AuthResponse:
    result = authenticate_user(session, payload.username, payload.password)
    return _deliver_session(response, result, payload.session_transport)


@router.post("/oidc/challenge", response_model=OidcChallengeResponse)
def oidc_challenge(
    payload: OidcChallengeRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> OidcChallengeResponse:
    challenge_id, nonce, expires_in = issue_oidc_challenge(
        session,
        payload.provider,
        request.headers.get("origin", ""),
    )
    return OidcChallengeResponse(challenge_id=challenge_id, nonce=nonce, expires_in=expires_in)


@router.post("/oidc/exchange", response_model=AuthResponse, response_model_exclude_none=True)
def oidc_exchange(
    payload: OidcExchangeRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
) -> AuthResponse:
    identity = verify_oidc_identity(
        session,
        provider=payload.provider,
        id_token=payload.id_token,
        challenge_id=payload.challenge_id,
        nonce=payload.nonce,
        origin=request.headers.get("origin", ""),
    )
    result = authenticate_external_identity(session, identity)
    return _deliver_session(response, result, payload.session_transport)


@router.post("/refresh", response_model=AuthResponse, response_model_exclude_none=True)
def refresh(
    request: Request,
    response: Response,
    x_csrf_token: str = Header("", alias="X-CSRF-Token"),
    session: Session = Depends(get_db_session),
) -> AuthResponse:
    refresh_token, csrf_token = _browser_session_credentials(request, x_csrf_token)
    result = refresh_user_session(session, refresh_token, csrf_token)
    _set_session_cookies(response, result)
    return AuthResponse(**result)


@router.post("/native/refresh", response_model=AuthResponse, response_model_exclude_none=True)
def native_refresh(
    payload: NativeRefreshRequest,
    response: Response,
    session: Session = Depends(get_db_session),
) -> AuthResponse:
    result = refresh_user_session(session, payload.refresh_token, require_csrf=False)
    return _deliver_session(response, result, "native")


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    x_csrf_token: str = Header("", alias="X-CSRF-Token"),
    session: Session = Depends(get_db_session),
) -> None:
    try:
        refresh_token, csrf_token = _browser_session_credentials(request, x_csrf_token)
        revoke_refresh_session(session, refresh_token, csrf_token)
    except HTTPException:
        pass  # Logout is deliberately idempotent and reveals no session state.
    _clear_session_cookies(response)


@router.post("/native/logout", status_code=204)
def native_logout(
    payload: NativeRefreshRequest,
    session: Session = Depends(get_db_session),
) -> None:
    revoke_refresh_session(session, payload.refresh_token, require_csrf=False)


@router.post("/logout-all")
def logout_all(
    response: Response,
    user_id: str = Depends(require_account_auth),
    session: Session = Depends(get_db_session),
) -> dict[str, int | str]:
    revoked = revoke_all_user_sessions(session, user_id)
    _clear_session_cookies(response)
    return {"status": "logged_out", "revoked_sessions": revoked}
