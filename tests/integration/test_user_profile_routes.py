from fastapi.testclient import TestClient

from psych_support_bot.app import app

client = TestClient(app)


def test_put_and_get_profile() -> None:
    payload = {
        "user_id": "profile-user",
        "display_name": "Ava",
        "primary_concerns": ["anxiety", "sleep"],
        "goals": ["sleep better", "reduce rumination"],
        "support_preferences": ["brief steps", "gentle tone"],
        "risk_notes": "",
    }
    put_response = client.put("/v1/users/profile", json=payload)
    assert put_response.status_code == 200
    get_response = client.get("/v1/users/profile-user/profile")
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["display_name"] == "Ava"
    assert "anxiety" in body["primary_concerns"]
