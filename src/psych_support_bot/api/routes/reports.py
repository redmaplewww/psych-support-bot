from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from psych_support_bot.ai.schemas.messages import WeeklyReportResponse
from psych_support_bot.api.auth import request_user_id
from psych_support_bot.domain.reports.service import build_weekly_summary
from psych_support_bot.infra.db.repositories import (
    get_recent_checkins,
    save_weekly_report,
)
from psych_support_bot.infra.db.session import get_db_session

router = APIRouter(prefix="/v1/reports", tags=["reports"])


@router.get("/weekly", response_model=WeeklyReportResponse)
def get_weekly_report(
    request: Request,
    user_id: str = "",
    session: Session = Depends(get_db_session),
) -> WeeklyReportResponse:
    user_id = request_user_id(request, user_id)
    checkins = get_recent_checkins(session, user_id)
    summary = build_weekly_summary(checkins)
    save_weekly_report(session, user_id, summary)
    return WeeklyReportResponse(user_id=user_id, summary=summary)
