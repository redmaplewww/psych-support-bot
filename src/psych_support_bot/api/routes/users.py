from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from psych_support_bot.domain.users.schemas import (
    UserProfilePayload,
    UserProfileResponse,
)
from psych_support_bot.domain.users.service import build_profile_summary
from psych_support_bot.infra.db.repositories import (
    get_user_profile,
    upsert_user_profile,
)
from psych_support_bot.infra.db.session import get_db_session

router = APIRouter(prefix="/v1/users", tags=["users"])


@router.put("/profile", response_model=UserProfileResponse)
def put_profile(
    payload: UserProfilePayload,
    session: Session = Depends(get_db_session),
) -> UserProfileResponse:
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
    session: Session = Depends(get_db_session),
) -> UserProfileResponse:
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
