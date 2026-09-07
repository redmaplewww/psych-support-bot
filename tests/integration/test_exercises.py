from fastapi.testclient import TestClient

from psych_support_bot.app import app

client = TestClient(app)


def test_list_exercises() -> None:
    response = client.get("/v1/exercises")
    assert response.status_code == 200
    body = response.json()
    assert "cbt" in body
    assert "act" in body
    assert "dbt" in body
    assert "thought_record_full" in body["cbt"]
    assert "values_card_sort" in body["act"]
    assert "tipp_full" in body["dbt"]


def test_get_cbt_thought_record() -> None:
    response = client.get("/v1/exercises/cbt_thought_record")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "CBT Thought Record"
    assert "steps" in body


def test_get_dbt_tipp() -> None:
    response = client.get("/v1/exercises/dbt_tipp")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "DBT TIPP Skills for Crisis"


def test_get_expanded_cbt_exercise() -> None:
    response = client.get("/v1/exercises/cbt_thought_record_full")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "CBT Thought Record"
    assert "follow_up_prompts" in body


def test_exercise_not_found() -> None:
    response = client.get("/v1/exercises/nonexistent")
    assert response.status_code == 404
