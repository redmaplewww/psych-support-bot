from fastapi.testclient import TestClient

from psych_support_bot.app import app

client = TestClient(app)


def test_history_and_message_routes() -> None:
    response = client.post(
        "/v1/conversations/respond",
        json={"user_id": "history-user", "message": "I feel anxious and need support"},
    )
    assert response.status_code == 200
    session_id = response.json()["session_id"]

    history = client.get("/v1/conversations/history", params={"user_id": "history-user"})
    assert history.status_code == 200
    assert history.json()

    messages = client.get(f"/v1/conversations/{session_id}/messages")
    assert messages.status_code == 200
    assert len(messages.json()) >= 2


def test_risk_events_route() -> None:
    client.post(
        "/v1/conversations/respond",
        json={"user_id": "risk-user", "message": "I want to die and hurt myself"},
    )

    risk_events = client.get("/v1/conversations/risk-events", params={"user_id": "risk-user"})
    assert risk_events.status_code == 200
    assert risk_events.json()
