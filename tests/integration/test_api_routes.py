from fastapi.testclient import TestClient

from psych_support_bot.app import app

client = TestClient(app)


def test_health_route() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_assessment_route() -> None:
    response = client.post(
        "/v1/assessments",
        json={"user_id": "u-api", "assessment_type": "gad7", "score": 12},
    )
    assert response.status_code == 200
    assert response.json()["severity_band"] == "moderate"
    assert "interpretation" in response.json()


def test_questionnaire_guide_route() -> None:
    response = client.get("/v1/assessments/questionnaires/phq9")

    assert response.status_code == 200
    assert response.json()["code"] == "phq9"
    assert len(response.json()["items"]) == 9


def test_assessment_answers_route_adds_safety_flag() -> None:
    response = client.post(
        "/v1/assessments",
        json={
            "user_id": "u-api-answers",
            "assessment_type": "phq9",
            "answers": [0, 1, 1, 1, 0, 0, 1, 0, 1],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 5
    assert body["interpretation"]["needs_safety_followup"] is True
    assert body["interpretation"]["safety_flags"][0]["code"] == "self_harm_signal"


def test_questionnaire_session_flow() -> None:
    from uuid import uuid4

    uid = f"u-flow-{uuid4().hex[:8]}"
    start = client.post(
        "/v1/assessments/sessions",
        json={"user_id": uid, "assessment_type": "gad7"},
    )

    assert start.status_code == 200
    session_id = start.json()["session_id"]
    assert start.json()["next_item"]["index"] == 0

    for _ in range(7):
        answer = client.post(
            f"/v1/assessments/sessions/{session_id}/answers?user_id={uid}",
            json={"value": 1},
        )
        assert answer.status_code == 200

    review = client.get(f"/v1/assessments/sessions/{session_id}?user_id={uid}")
    assert review.status_code == 200
    assert review.json()["current_index"] == 7
    assert review.json()["next_item"] is None

    complete = client.post(f"/v1/assessments/sessions/{session_id}/complete?user_id={uid}")
    assert complete.status_code == 200
    body = complete.json()
    assert body["session"]["status"] == "completed"
    assert body["result"]["score"] == 7
    assert body["result"]["severity_band"] == "mild"


def test_plans_route() -> None:
    response = client.get("/v1/plans")
    assert response.status_code == 200
    assert "anxiety_14d" in response.json()


def test_get_questionnaires_returns_list() -> None:
    response = client.get("/v1/assessments/questionnaires")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) > 0
    codes = [q["code"] for q in body]
    assert "phq9" in codes
    assert "gad7" in codes


def test_get_questionnaire_by_type_returns_correct_structure() -> None:
    response = client.get("/v1/assessments/questionnaires/gad7")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "gad7"
    assert "items" in body
    assert len(body["items"]) == 7
    assert "title" in body
    assert "options" in body


def test_start_session_returns_session_with_next_item() -> None:
    from uuid import uuid4

    uid = f"u-new-{uuid4().hex[:8]}"
    response = client.post(
        "/v1/assessments/sessions",
        json={"user_id": uid, "assessment_type": "phq9"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "session_id" in body
    assert body["next_item"] is not None
    assert body["next_item"]["index"] == 0
    assert body["assessment_type"] == "phq9"


def test_get_session_returns_session_state() -> None:
    from uuid import uuid4

    uid = f"u-get-{uuid4().hex[:8]}"
    start = client.post(
        "/v1/assessments/sessions",
        json={"user_id": uid, "assessment_type": "gad7"},
    )
    session_id = start.json()["session_id"]
    for _ in range(3):
        client.post(
            f"/v1/assessments/sessions/{session_id}/answers?user_id={uid}",
            json={"value": 2},
        )

    response = client.get(f"/v1/assessments/sessions/{session_id}?user_id={uid}")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["current_index"] == 3
    assert body["next_item"] is not None


def test_complete_session_returns_result_with_interpretation() -> None:
    from uuid import uuid4

    uid = f"u-complete-{uuid4().hex[:8]}"
    start = client.post(
        "/v1/assessments/sessions",
        json={"user_id": uid, "assessment_type": "gad7"},
    )
    session_id = start.json()["session_id"]
    for _ in range(7):
        client.post(
            f"/v1/assessments/sessions/{session_id}/answers?user_id={uid}",
            json={"value": 2},
        )

    response = client.post(f"/v1/assessments/sessions/{session_id}/complete?user_id={uid}")
    assert response.status_code == 200
    body = response.json()
    assert body["session"]["status"] == "completed"
    assert "result" in body
    assert "score" in body["result"]
    assert "severity_band" in body["result"]
    assert "interpretation" in body["result"]
