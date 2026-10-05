"""OIDC ID-token verification with fixed issuers and one-time server nonces."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from urllib.parse import urlsplit

import jwt as pyjwt
from fastapi import HTTPException
from sqlalchemy.orm import Session

from psych_support_bot.domain.auth.identity import VerifiedIdentity
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.models import AuthChallenge

_CHALLENGE_TTL = timedelta(minutes=5)
_INVALID_ASSERTION = "Identity verification failed."


@dataclass(frozen=True, slots=True)
class OidcProviderConfig:
    name: str
    issuer: str
    accepted_issuers: tuple[str, ...]
    jwks_url: str
    audiences: tuple[str, ...]
    algorithms: tuple[str, ...]


def _client_ids(raw: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in raw.split(",") if value.strip()))


def provider_config(provider: str) -> OidcProviderConfig:
    settings = get_settings()
    configs = {
        "google": OidcProviderConfig(
            name="google",
            issuer="https://accounts.google.com",
            accepted_issuers=("https://accounts.google.com", "accounts.google.com"),
            jwks_url="https://www.googleapis.com/oauth2/v3/certs",
            audiences=_client_ids(settings.auth_google_client_ids),
            algorithms=("RS256",),
        ),
        "apple": OidcProviderConfig(
            name="apple",
            issuer="https://appleid.apple.com",
            accepted_issuers=("https://appleid.apple.com",),
            jwks_url="https://appleid.apple.com/auth/keys",
            audiences=_client_ids(settings.auth_apple_client_ids),
            algorithms=("RS256",),
        ),
        "huawei": OidcProviderConfig(
            name="huawei",
            issuer="https://accounts.huawei.com",
            accepted_issuers=("https://accounts.huawei.com",),
            jwks_url="https://oauth-login.cloud.huawei.com/oauth2/v3/certs",
            audiences=_client_ids(settings.auth_huawei_client_ids),
            algorithms=("RS256", "PS256"),
        ),
    }
    config = configs.get(provider.lower())
    if config is None:
        raise HTTPException(status_code=422, detail="Unsupported identity provider.")
    if not config.audiences:
        raise HTTPException(status_code=503, detail="Identity provider is not configured.")
    return config


# Defense-in-depth for the only server-side outbound fetch in the auth path:
# a JWKS URL may be https and must hit exactly one of the hardcoded provider
# hosts, so no request ever reaches localhost/loopback/private/reserved targets
# even if a future edit introduces a configurable issuer URL.
_ALLOWED_JWKS_HOSTS = frozenset({"www.googleapis.com", "appleid.apple.com", "oauth-login.cloud.huawei.com"})


@lru_cache(maxsize=3)
def _jwks_client(url: str) -> pyjwt.PyJWKClient:
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in _ALLOWED_JWKS_HOSTS:
        raise ValueError("Refusing to fetch JWKS from a non-allowlisted origin")
    return pyjwt.PyJWKClient(url)


def issue_oidc_challenge(session: Session, provider: str, origin: str = "") -> tuple[str, str, int]:
    config = provider_config(provider)
    challenge_id = secrets.token_hex(16)
    nonce = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    session.query(AuthChallenge).filter(AuthChallenge.expires_at < now).delete(synchronize_session=False)
    session.add(
        AuthChallenge(
            id=challenge_id,
            flow=f"oidc_{config.name}",
            challenge_hash=hashlib.sha256(nonce.encode()).hexdigest(),
            expected_origin=origin,
            expected_rp_id="",
            created_at=now,
            expires_at=now + _CHALLENGE_TTL,
        )
    )
    session.commit()
    return challenge_id, nonce, int(_CHALLENGE_TTL.total_seconds())


def _consume_challenge(
    session: Session,
    *,
    challenge_id: str,
    provider: str,
    nonce: str,
    origin: str,
) -> None:
    challenge = session.get(AuthChallenge, challenge_id)
    now = datetime.now(UTC)
    expires_at = None
    if challenge is not None:
        expires_at = (
            challenge.expires_at.replace(tzinfo=UTC) if challenge.expires_at.tzinfo is None else challenge.expires_at
        )
    valid = (
        challenge is not None
        and challenge.flow == f"oidc_{provider}"
        and challenge.used_at is None
        and expires_at is not None
        and expires_at > now
        and hmac.compare_digest(challenge.challenge_hash, hashlib.sha256(nonce.encode()).hexdigest())
        and (not challenge.expected_origin or hmac.compare_digest(challenge.expected_origin, origin))
    )
    if not valid:
        raise HTTPException(status_code=401, detail=_INVALID_ASSERTION)
    updated = (
        session.query(AuthChallenge)
        .filter(AuthChallenge.id == challenge_id, AuthChallenge.used_at.is_(None))
        .update({AuthChallenge.used_at: now}, synchronize_session=False)
    )
    if updated != 1:
        session.rollback()
        raise HTTPException(status_code=401, detail=_INVALID_ASSERTION)
    session.commit()


def verify_oidc_identity(
    session: Session,
    *,
    provider: str,
    id_token: str,
    challenge_id: str,
    nonce: str,
    origin: str = "",
) -> VerifiedIdentity:
    provider = provider.lower()
    config = provider_config(provider)
    _consume_challenge(
        session,
        challenge_id=challenge_id,
        provider=provider,
        nonce=nonce,
        origin=origin,
    )
    if len(id_token) > 16_384:
        raise HTTPException(status_code=401, detail=_INVALID_ASSERTION)
    try:
        signing_key = _jwks_client(config.jwks_url).get_signing_key_from_jwt(id_token)
        claims = pyjwt.decode(
            id_token,
            signing_key.key,
            algorithms=list(config.algorithms),
            audience=list(config.audiences),
            issuer=config.accepted_issuers,
            leeway=60,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
        )
        if not hmac.compare_digest(str(claims["nonce"]), nonce):
            raise pyjwt.InvalidTokenError("nonce mismatch")
        subject = str(claims["sub"])
        if not subject or len(subject) > 512:
            raise pyjwt.InvalidTokenError("invalid subject")
        audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
        if len(audiences) > 1 and claims.get("azp") not in config.audiences:
            raise pyjwt.InvalidTokenError("invalid authorized party")
    except (pyjwt.PyJWTError, OSError, ValueError) as exc:
        raise HTTPException(status_code=401, detail=_INVALID_ASSERTION) from exc
    return VerifiedIdentity(
        provider=config.name,
        issuer=config.issuer,
        subject=subject,
        authenticated_at=datetime.fromtimestamp(int(claims["iat"]), tz=UTC),
    )
