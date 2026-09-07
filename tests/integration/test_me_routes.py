"""「我」页数据管理接口：全量导出 / 两步确认清空记录 / 注销级联删除。

破坏性接口的确认令牌是安全重点：无令牌、错令牌、跨操作令牌都必须拒绝。
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from psych_support_bot.app import app

client = TestClient(app)


def _fresh_user() -> str:
    # 集成测试共享真实 dev 库：每次运行用随机用户隔离历史数据
    return "me-test-" + uuid4().hex[:10]


USER = _fresh_user()


def _seed_user_data(user_id: str) -> None:
    client.post(
        "/v1/checkins?user_id=" + user_id,
        json={
            "checkin_date": "2026-09-01",
            "mood_score": 5,
            "anxiety_score": 4,
            "sleep_hours": 7.0,
            "energy_score": 6,
            "note": "还行",
        },
    )
    client.post(
        "/v1/assessments",
        json={
            "user_id": user_id,
            "assessment_type": "phq9",
            "answers": [0] * 9,
        },
    )
    client.post(
        f"/v1/exercises/panic_grounding_5_4_3_2_1/complete?user_id={user_id}&source=chat",
        json={"reflection_note": "试了接地练习"},
    )
    client.post("/v1/conversations/respond", json={"user_id": user_id, "message": "最近有点累"})


def test_export_returns_full_json() -> None:
    _seed_user_data(USER)
    response = client.get(f"/v1/me/export?user_id={USER}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    data = response.json()
    assert data["user"]["user_id"] == USER
    assert data["user"]["created_at"]
    assert len(data["checkins"]) == 1
    assert len(data["assessments"]) == 1
    assert len(data["exercise_records"]) == 1
    assert data["counts"]["messages"] >= 2  # 用户消息 + 助手回复
    assert data["counts"]["conversations"] >= 1


def test_confirm_intent_rejects_unknown_action() -> None:
    response = client.post(
        "/v1/me/confirm-intent",
        json={"user_id": USER, "action": "drop_database"},
    )
    assert response.status_code == 422


def test_clear_records_requires_valid_token() -> None:
    # 无令牌
    assert client.delete(f"/v1/me/records?user_id={USER}").status_code in {403, 422}
    # 错令牌
    assert client.delete(f"/v1/me/records?user_id={USER}&confirm_token=badtoken_123").status_code == 403
    # 跨操作令牌（delete_account 的令牌不能用于 clear_records）
    other = client.post(
        "/v1/me/confirm-intent",
        json={"user_id": USER, "action": "delete_account"},
    ).json()["confirm_token"]
    assert client.delete(f"/v1/me/records?user_id={USER}&confirm_token={other}").status_code == 403


def test_clear_records_two_step_flow() -> None:
    _seed_user_data(USER)
    token = client.post(
        "/v1/me/confirm-intent",
        json={"user_id": USER, "action": "clear_records"},
    ).json()["confirm_token"]
    response = client.delete(f"/v1/me/records?user_id={USER}&confirm_token={token}")
    assert response.status_code == 200
    deleted = response.json()["deleted"]
    assert deleted["assessments"] >= 1
    assert deleted["exercise_records"] >= 1
    assert deleted["checkins"] >= 1

    # 记录清空，聊天保留
    export = client.get(f"/v1/me/export?user_id={USER}").json()
    assert export["counts"]["assessments"] == 0
    assert export["counts"]["exercise_records"] == 0
    assert export["counts"]["checkins"] == 0
    assert export["counts"]["messages"] >= 2
    assert client.get(f"/v1/users/{USER}/profile").status_code in {200, 404}


def test_expired_confirm_token_rejected(monkeypatch) -> None:
    import psych_support_bot.api.routes.me as me_routes

    monkeypatch.setattr(me_routes, "CONFIRM_TOKEN_TTL_SECONDS", -1)
    token = client.post(
        "/v1/me/confirm-intent",
        json={"user_id": USER, "action": "clear_records"},
    ).json()["confirm_token"]
    monkeypatch.setattr(me_routes, "CONFIRM_TOKEN_TTL_SECONDS", 600)
    assert client.delete(f"/v1/me/records?user_id={USER}&confirm_token={token}").status_code == 403


def test_delete_account_cascades_all_tables() -> None:
    user = _fresh_user()
    _seed_user_data(user)
    token = client.post(
        "/v1/me/confirm-intent",
        json={"user_id": user, "action": "delete_account"},
    ).json()["confirm_token"]
    response = client.delete(f"/v1/me/account?user_id={user}&confirm_token={token}")
    assert response.status_code == 200
    deleted = response.json()["deleted"]
    assert deleted["users"] == 1
    assert deleted["messages"] >= 2
    assert deleted["sessions"] >= 1
    assert deleted["assessments"] >= 1
    assert deleted["checkins"] >= 1

    # 数据彻底消失：导出为空壳、历史接口为空
    export = client.get(f"/v1/me/export?user_id={user}").json()
    assert export["counts"] == {
        "assessments": 0,
        "exercise_records": 0,
        "checkins": 0,
        "conversations": 0,
        "messages": 0,
    }
    history = client.get(f"/v1/assessments?user_id={user}")
    assert history.status_code == 200
    assert history.json() == []


def test_confirm_token_binds_to_user() -> None:
    user_a = _fresh_user()
    user_b = _fresh_user()
    token = client.post(
        "/v1/me/confirm-intent",
        json={"user_id": user_a, "action": "clear_records"},
    ).json()["confirm_token"]
    # A 的令牌不能删 B 的数据
    response = client.delete(f"/v1/me/records?user_id={user_b}&confirm_token={token}")
    assert response.status_code == 403
