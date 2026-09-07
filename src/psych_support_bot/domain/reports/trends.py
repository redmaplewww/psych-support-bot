from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy.orm import Session

from psych_support_bot.infra.db.models import AssessmentRecord, CheckinRecord


class TrendResult:
    def __init__(
        self,
        mood_trend: str,
        anxiety_trend: str,
        sleep_trend: str,
        energy_trend: str,
        assessment_trend: str,
        overall_status: str,
        days_analyzed: int,
    ):
        self.mood_trend = mood_trend
        self.anxiety_trend = anxiety_trend
        self.sleep_trend = sleep_trend
        self.energy_trend = energy_trend
        self.assessment_trend = assessment_trend
        self.overall_status = overall_status
        self.days_analyzed = days_analyzed


def _compute_trend(values: Sequence[int | float]) -> str:
    if len(values) < 2:
        return "insufficient_data"
    first_half = values[: len(values) // 2]
    second_half = values[len(values) // 2 :]
    first_avg = sum(first_half) / len(first_half)
    second_avg = sum(second_half) / len(second_half)
    diff = second_avg - first_avg
    if abs(diff) < 0.5:
        return "stable"
    if diff > 0.5:
        return "improving"
    return "worsening"


def compute_user_trends(session: Session, user_id: str, days: int = 14) -> TrendResult:
    since = date.today() - timedelta(days=days)  # noqa: DTZ011

    checkins_stmt = (
        session.query(CheckinRecord)
        .filter(
            CheckinRecord.user_id == user_id,
            CheckinRecord.checkin_date >= since,
        )
        .order_by(CheckinRecord.checkin_date)
        .all()
    )

    mood_vals = [c.mood_score for c in checkins_stmt]
    anxiety_vals = [c.anxiety_score for c in checkins_stmt]
    sleep_vals = [c.sleep_hours for c in checkins_stmt]
    energy_vals = [c.energy_score for c in checkins_stmt]

    mood_trend = _compute_trend(mood_vals)
    anxiety_trend = _compute_trend(anxiety_vals)
    sleep_trend = _compute_trend(sleep_vals)
    energy_trend = _compute_trend(energy_vals)

    assessment_stmt = (
        session.query(AssessmentRecord)
        .filter(
            AssessmentRecord.user_id == user_id,
        )
        .order_by(AssessmentRecord.created_at.desc())
        .limit(4)
        .all()
    )
    assessment_trend = "stable"
    if len(assessment_stmt) >= 2:
        recent_avg = sum(a.score for a in assessment_stmt[:2]) / 2
        older_avg = sum(a.score for a in assessment_stmt[2:]) / 2
        if abs(recent_avg - older_avg) >= 2:
            assessment_trend = "improving" if recent_avg < older_avg else "worsening"

    worsening_count = sum(
        1
        for t in [
            mood_trend,
            anxiety_trend,
            sleep_trend,
            energy_trend,
            assessment_trend,
        ]
        if t == "worsening"
    )

    if worsening_count >= 3:
        overall_status = "needs_attention"
    elif mood_trend == "worsening" or anxiety_trend == "worsening":
        overall_status = "monitor"
    else:
        overall_status = "stable"

    return TrendResult(
        mood_trend=mood_trend,
        anxiety_trend=anxiety_trend,
        sleep_trend=sleep_trend,
        energy_trend=energy_trend,
        assessment_trend=assessment_trend,
        overall_status=overall_status,
        days_analyzed=len(checkins_stmt),
    )
