from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from psych_support_bot.api.auth import request_user_id
from psych_support_bot.domain import consents
from psych_support_bot.domain.users.schemas import (
    UserProfilePayload,
    UserProfileResponse,
)
from psych_support_bot.domain.users.service import build_profile_summary
from psych_support_bot.infra.db.repositories import (
    get_user_profile,
    record_usage_event,
    upsert_user_profile,
)
from psych_support_bot.infra.db.session import get_db_session

router = APIRouter(prefix="/v1/users", tags=["users"])


@router.put("/profile", response_model=UserProfileResponse)
def put_profile(
    payload: UserProfilePayload,
    request: Request,
    session: Session = Depends(get_db_session),
) -> UserProfileResponse:
    payload.user_id = request_user_id(request, payload.user_id)
    profile = upsert_user_profile(
        session=session,
        user_id=payload.user_id,
        display_name=payload.display_name,
        primary_concerns=", ".join(payload.primary_concerns),
        goals=", ".join(payload.goals),
        support_preferences=", ".join(payload.support_preferences),
        risk_notes=build_profile_summary(payload),
    )
    return UserProfileResponse(
        user_id=profile.user_id,
        display_name=profile.display_name,
        primary_concerns=[item for item in profile.primary_concerns.split(", ") if item],
        goals=[item for item in profile.goals.split(", ") if item],
        support_preferences=[item for item in profile.support_preferences.split(", ") if item],
        risk_notes=profile.risk_notes,
        updated_at=profile.updated_at.isoformat(),
    )


@router.get("/{user_id}/profile", response_model=UserProfileResponse)
def read_profile(
    user_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> UserProfileResponse:
    user_id = request_user_id(request, user_id)
    profile = get_user_profile(session, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return UserProfileResponse(
        user_id=profile.user_id,
        display_name=profile.display_name,
        primary_concerns=[item for item in profile.primary_concerns.split(", ") if item],
        goals=[item for item in profile.goals.split(", ") if item],
        support_preferences=[item for item in profile.support_preferences.split(", ") if item],
        risk_notes=profile.risk_notes,
        updated_at=profile.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# 隐私协议 / 数据处理协议（使用前确认；版本变更后前端重新弹窗）
# ---------------------------------------------------------------------------


class PrivacyAgreementResponse(BaseModel):
    privacy_points: list[str]
    data_processing_points: list[str]
    consent_version: str


class PrivacyConsentRequest(BaseModel):
    acknowledged: bool
    consent_version: str = Field("", max_length=32)
    expected_language: str = Field("zh", pattern="^(zh|en)$")


@router.get("/privacy-agreement", response_model=PrivacyAgreementResponse)
def get_privacy_agreement(expected_language: str = Query("zh", pattern="^(zh|en)$")) -> PrivacyAgreementResponse:
    """隐私协议 + 数据处理协议条目与当前版本（前端首启弹窗渲染）。"""
    zh = expected_language == "zh"
    return PrivacyAgreementResponse(
        privacy_points=consents.PRIVACY_AGREEMENT_POINTS_ZH if zh else consents.PRIVACY_AGREEMENT_POINTS_EN,
        data_processing_points=(consents.DATA_PROCESSING_POINTS_ZH if zh else consents.DATA_PROCESSING_POINTS_EN),
        consent_version=consents.PRIVACY_CONSENT_VERSION,
    )


@router.post("/privacy-consent")
def acknowledge_privacy_agreement(
    request: Request,
    user_id: str = Query(""),
    payload: PrivacyConsentRequest = None,  # type: ignore[assignment]
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """隐私/数据处理协议确认落库（只记版本与时间，无内容）。"""
    user_id = request_user_id(request, user_id)
    payload = payload or PrivacyConsentRequest(acknowledged=True)
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="acknowledged must be true")
    record_usage_event(
        session,
        user_id,
        "privacy_policy_ack",
        consent_version=payload.consent_version or consents.PRIVACY_CONSENT_VERSION,
    )
    session.commit()
    return {"status": "acknowledged", "consent_version": consents.PRIVACY_CONSENT_VERSION}
