"""Consent is an enforced processing boundary; data management stays available."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from psych_support_bot.api.auth import require_auth
from psych_support_bot.api.user_id import normalize_user_id
from psych_support_bot.domain.consents import current_privacy_version
from psych_support_bot.infra.db.models import PrivacyConsent, PrivacyDeletionJob
from psych_support_bot.infra.db.session import get_db_session


def check_privacy_consent(session: Session, user_id: str) -> None:
    if session.query(PrivacyDeletionJob).filter(PrivacyDeletionJob.user_id == user_id).first():
        raise HTTPException(status_code=403, detail="Account deletion is in progress.")
    consent = session.query(PrivacyConsent).filter(PrivacyConsent.user_id == user_id).first() if user_id else None
    if consent is None or consent.version != current_privacy_version():
        raise HTTPException(
            status_code=403, detail={"code": "privacy_consent_required", "version": current_privacy_version()}
        )


async def require_privacy_consent(
    request: Request,
    current_user: str = Depends(require_auth),
    session: Session = Depends(get_db_session),
) -> None:
    path = request.url.path
    if path.startswith("/v1/me/") or path in {"/v1/users/privacy-agreement", "/v1/users/privacy-consent"}:
        return
    # Reading one's existing records and static resources does not require a
    # new consent. GET analysis and voice endpoints do invoke providers.
    if request.method in {"GET", "HEAD", "OPTIONS"} and not (
        path.endswith("/analysis") or path == "/v1/reports/weekly" or path.startswith("/v1/voice/")
    ):
        return
    declared = request.query_params.get("user_id") or request.headers.get("X-User-ID", "")
    if "application/json" in request.headers.get("content-type", ""):
        try:
            body = await request.json()
            if isinstance(body, dict):
                declared = body.get("user_id") or declared
        except ValueError:
            return  # Let the endpoint's schema validation report malformed JSON.
    if current_user and declared and declared != current_user:
        raise HTTPException(status_code=403, detail="User ID does not match the authenticated identity.")
    subject = current_user or declared
    check_privacy_consent(session, normalize_user_id(subject) if subject else subject)
