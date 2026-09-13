from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from psych_support_bot.api.auth import request_user_id
from psych_support_bot.domain.reports.trends import TrendResult, compute_user_trends
from psych_support_bot.infra.db.session import get_db_session

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


class TrendResponse(BaseModel):
    mood_trend: str
    anxiety_trend: str
    sleep_trend: str
    energy_trend: str
    assessment_trend: str
    overall_status: str
    days_analyzed: int


@router.get("/trends", response_model=TrendResponse)
def get_trends(
    request: Request,
    user_id: str = "",
    days: int = 14,
    session: Session = Depends(get_db_session),
) -> TrendResponse:
    user_id = request_user_id(request, user_id)
    result: TrendResult = compute_user_trends(session, user_id, days=days)
    return TrendResponse(
        mood_trend=result.mood_trend,
        anxiety_trend=result.anxiety_trend,
        sleep_trend=result.sleep_trend,
        energy_trend=result.energy_trend,
        assessment_trend=result.assessment_trend,
        overall_status=result.overall_status,
        days_analyzed=result.days_analyzed,
    )
