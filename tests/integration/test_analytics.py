from fastapi.testclient import TestClient

from psych_support_bot.app import app
from psych_support_bot.domain.checkins.schemas import DailyCheckin
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.db.repositories import save_checkin
from psych_support_bot.infra.db.session import SessionLocal

client = TestClient(app)
init_db()


def test_trends_api_no_data() -> None:
    response = client.get("/v1/analytics/trends", params={"user_id": "no-data-user", "days": 7})
    assert response.status_code == 200
    body = response.json()
    assert body["days_analyzed"] == 0
    assert body["overall_status"] == "stable"


def test_trends_api_with_data() -> None:
    with SessionLocal() as session:
        for i in range(3):
            checkin = DailyCheckin(
                mood_score=3 + i,
                anxiety_score=7 - i,
                sleep_hours=5.0 + i,
                energy_score=2 + i,
                note="",
            )
            save_checkin(session, "trend-user", checkin)

    response = client.get("/v1/analytics/trends", params={"user_id": "trend-user", "days": 7})
    assert response.status_code == 200
    body = response.json()
    assert body["mood_trend"] in {
        "stable",
        "improving",
        "worsening",
        "insufficient_data",
    }
    assert body["sleep_trend"] in {
        "stable",
        "improving",
        "worsening",
        "insufficient_data",
    }
