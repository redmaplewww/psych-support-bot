from celery import shared_task

from psych_support_bot.domain.reports.service import build_weekly_summary
from psych_support_bot.infra.db.repositories import (
    get_recent_checkins,
    save_weekly_report,
)
from psych_support_bot.infra.db.session import SessionLocal


@shared_task(name="psych_support_bot.generate_weekly_report")
def generate_weekly_report(user_id: str) -> dict:
    with SessionLocal() as session:
        checkins = get_recent_checkins(session, user_id, limit=7)
        summary = build_weekly_summary(checkins)
        record = save_weekly_report(session, user_id, summary)
        return {
            "report_id": record.id,
            "user_id": user_id,
            "summary": summary,
        }


@shared_task(name="psych_support_bot.ping")
def ping() -> str:
    return "pong"
