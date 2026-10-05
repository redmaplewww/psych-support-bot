"""Safe boundary between external identity verifiers and internal accounts."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.models import AuthIdentity


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    provider: str
    issuer: str
    subject: str
    authenticated_at: datetime | None = None


class IdentityVerifier(Protocol):
    """Provider adapters must cryptographically verify before returning this value."""

    def verify(self, assertion: str, *, nonce: str, audience: str) -> VerifiedIdentity: ...


def identity_subject_hash(identity: VerifiedIdentity) -> str:
    key = get_settings().auth_identity_hash_key.encode()
    canonical = f"{identity.provider}\0{identity.issuer}\0{identity.subject}".encode()
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


def find_identity(session: Session, identity: VerifiedIdentity) -> AuthIdentity | None:
    digest = identity_subject_hash(identity)
    return (
        session.query(AuthIdentity)
        .filter(
            AuthIdentity.provider == identity.provider,
            AuthIdentity.issuer == identity.issuer,
            AuthIdentity.subject_hash == digest,
            AuthIdentity.status == "active",
        )
        .one_or_none()
    )


def link_verified_identity(session: Session, user_id: str, identity: VerifiedIdentity) -> AuthIdentity:
    """Link only after the caller has recently authenticated the target account."""
    existing = find_identity(session, identity)
    if existing is not None:
        if existing.user_id != user_id:
            raise HTTPException(status_code=409, detail="Identity is already linked to another account.")
        existing.last_used_at = datetime.now(UTC)
        return existing
    record = AuthIdentity(
        id=uuid4().hex,
        user_id=user_id,
        provider=identity.provider,
        issuer=identity.issuer,
        subject_hash=identity_subject_hash(identity),
    )
    session.add(record)
    session.flush()
    return record
