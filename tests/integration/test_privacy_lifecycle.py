import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Date, DateTime, Float, Integer, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from psych_support_bot.app import app
from psych_support_bot.domain.consents import current_privacy_version
from psych_support_bot.infra.config.settings import Settings, get_settings
from psych_support_bot.infra.db.base import Base
from psych_support_bot.infra.db.me_repositories import USER_DATA_MODELS
from psych_support_bot.infra.db.models import Message, PrivacyConsent, PrivacyDeletionJob, User
from psych_support_bot.services.privacy_deletion import _trace_api, process_deletion_job

pytestmark = pytest.mark.privacy_boundary


@pytest.fixture
def privacy_env(monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for key, value in {
        "AUTH_ENABLED": "false",
        "LANGFUSE_DELETE_HISTORY": "false",
        "OPENAI_API_KEY": "",
        "DASHSCOPE_API_KEY": "",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False)
    monkeypatch.setattr("psych_support_bot.infra.db.session.SessionLocal", factory)
    monkeypatch.setattr("psych_support_bot.services.privacy_deletion.SessionLocal", factory)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, factory
    engine.dispose()
    get_settings.cache_clear()


def _seed_every_table(factory, uid):
    with factory() as session:
        session.add(User(id=uid))
        for model in USER_DATA_MODELS:
            values = {"user_id": uid}
            for col in model.__table__.columns:
                if col.name == "user_id":
                    continue
                if col.name == "id" and isinstance(col.type, Integer):
                    continue
                if col.nullable or col.default is not None or col.server_default is not None:
                    continue
                if isinstance(col.type, DateTime):
                    values[col.name] = datetime.now(UTC)
                elif isinstance(col.type, Date):
                    values[col.name] = datetime.now(UTC).date()
                elif isinstance(col.type, (Integer, Float)):
                    values[col.name] = 1
                else:
                    values[col.name] = uid + "-" + model.__tablename__ + "-" + col.name
            if model is PrivacyConsent:
                values["version"] = current_privacy_version()
            if hasattr(model, "step_responses_json"):
                values["step_responses_json"] = json.dumps(["private answer " + uid])
                values["guidance_transcript_json"] = json.dumps([{"role": "user", "content": "private " + uid}])
            session.add(model(**values))
        session.add(Message(session_id=uid + "-sessions-id", role="user", content="private " + uid))
        session.commit()


def test_inventory_covers_all_user_business_tables():
    actual = {table.name for table in Base.metadata.tables.values() if "user_id" in table.c}
    assert actual == {model.__tablename__ for model in USER_DATA_MODELS} | {"privacy_deletion_jobs"}


def test_export_and_delete_every_table_preserving_other_user(privacy_env):
    client, factory = privacy_env
    _seed_every_table(factory, "owner")
    _seed_every_table(factory, "other")
    exported = client.get("/v1/me/export?user_id=owner").json()
    assert set(exported["data_tables"]) == {model.__tablename__ for model in USER_DATA_MODELS}
    assert all(len(rows) == 1 for rows in exported["data_tables"].values())
    assert "password_hash" not in json.dumps(exported)
    assert "refresh_token_hash" not in json.dumps(exported)
    assert "subject_hash" not in json.dumps(exported)
    assert "public_key_cose" not in json.dumps(exported)
    assert "private answer owner" in exported["data_tables"]["practice_sessions"][0]["step_responses_json"]
    assert exported["exercise_records"][0]["guidance_transcript"]
    assert "private other" not in json.dumps(exported)
    token = client.post("/v1/me/confirm-intent", json={"user_id": "owner", "action": "delete_account"}).json()[
        "confirm_token"
    ]
    response = client.delete("/v1/me/account", params={"user_id": "owner", "confirm_token": token})
    assert response.status_code == 200
    assert response.json()["deleted"]["practice_sessions"] == 1
    with factory() as session:
        for model in USER_DATA_MODELS:
            assert session.query(model).filter(model.user_id == "owner").count() == 0, model.__tablename__
            assert session.query(model).filter(model.user_id == "other").count() == 1, model.__tablename__
        assert session.get(User, "owner") is None
        assert session.query(Message).filter(Message.content == "private owner").count() == 0
        assert session.query(Message).filter(Message.content == "private other").count() == 1
    receipt = response.json()["external_cleanup"]["receipt"]
    assert client.get("/v1/privacy/deletions/" + receipt).json()["external_cleanup"] == "not_required"


def test_consent_gate_rejects_missing_stale_and_revoked_consent(privacy_env):
    client, factory = privacy_env
    body = {"mood_score": 5, "anxiety_score": 4, "sleep_hours": 7, "energy_score": 5}
    assert client.post("/v1/checkins?user_id=visitor", json=body).status_code == 403
    assert client.get("/v1/checkins/analysis?user_id=visitor").status_code == 403
    assert client.post("/v1/conversations/respond", json={"user_id": "visitor", "message": "hello"}).status_code == 403
    assert client.get("/v1/me/export?user_id=visitor").status_code == 200
    assert client.post("/v1/users/privacy-consent?user_id=visitor").status_code == 422
    assert (
        client.post(
            "/v1/users/privacy-consent?user_id=visitor", json={"acknowledged": True, "consent_version": "old"}
        ).status_code
        == 409
    )
    version = client.get("/v1/users/privacy-agreement").json()["consent_version"]
    assert (
        client.post(
            "/v1/users/privacy-consent?user_id=visitor", json={"acknowledged": True, "consent_version": version}
        ).status_code
        == 200
    )
    assert client.post("/v1/checkins?user_id=visitor", json=body).status_code == 200
    assert client.delete("/v1/users/privacy-consent?user_id=visitor").status_code == 200
    assert client.post("/v1/checkins?user_id=visitor", json=body).status_code == 403
    with factory() as session:
        assert session.get(PrivacyConsent, "visitor") is None


def test_voice_requires_consent_before_provider_access(privacy_env, monkeypatch):
    client, _ = privacy_env
    monkeypatch.setattr("psych_support_bot.api.routes.voice.transcribe", lambda *_: pytest.fail("provider called"))
    assert (
        client.post(
            "/v1/voice/transcribe", headers={"X-User-ID": "visitor"}, files={"file": ("x.wav", b"audio", "audio/wav")}
        ).status_code
        == 403
    )
    assert client.post("/v1/voice/speak", headers={"X-User-ID": "visitor"}, json={"text": "private"}).status_code == 403
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as err, client.websocket_connect("/v1/voice/tts/live?user_id=visitor"):
        pass
    assert err.value.code == 4403


def test_changed_provider_requires_new_consent(privacy_env, monkeypatch):
    client, _ = privacy_env
    before = client.get("/v1/users/privacy-agreement").json()["consent_version"]
    monkeypatch.setenv("OPENAI_BASE_URL", "https://new-provider.example/v1")
    get_settings.cache_clear()
    after = client.get("/v1/users/privacy-agreement").json()
    assert before != after["consent_version"]
    assert after["processing_services"][0]["host"] == "new-provider.example"


def test_changing_langfuse_content_scope_requires_new_consent(privacy_env, monkeypatch):
    client, _ = privacy_env
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_CONTENT_ANALYTICS", "true")
    get_settings.cache_clear()
    before = client.get("/v1/users/privacy-agreement").json()
    monkeypatch.setenv("LANGFUSE_CONTENT_ANALYTICS", "false")
    get_settings.cache_clear()
    after = client.get("/v1/users/privacy-agreement").json()
    assert before["consent_version"] != after["consent_version"]
    assert "De-identified conversation analytics" in before["processing_services"][-1]["purpose"]
    assert after["processing_services"][-1]["purpose"] == "运行指标 / Operational metrics"


def test_production_content_analytics_requires_stable_pseudonym_key(monkeypatch):
    from pydantic import ValidationError

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_CONTENT_ANALYTICS", "true")
    monkeypatch.setenv("LANGFUSE_PSEUDONYM_KEY", "")
    with pytest.raises(ValidationError, match="LANGFUSE_PSEUDONYM_KEY"):
        Settings(_env_file=None)


def test_deleted_account_token_cannot_recreate_data(privacy_env, monkeypatch):
    client, _ = privacy_env
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-" * 8)
    get_settings.cache_clear()
    credentials = {"username": "test-account", "password": "test-password-123"}
    token = client.post("/v1/auth/register", json=credentials).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    intent = client.post("/v1/me/confirm-intent", headers=headers, json={"action": "delete_account"}).json()[
        "confirm_token"
    ]
    assert client.delete("/v1/me/account", headers=headers, params={"confirm_token": intent}).status_code == 200
    assert client.get("/v1/me/export", headers=headers).status_code == 401
    assert client.post("/v1/auth/register", json=credentials).status_code == 200
    assert client.get("/v1/me/export", headers=headers).status_code == 401


def test_external_deletion_retries_and_verifies_without_payload(privacy_env):
    _, factory = privacy_env
    calls = []

    class FakeTraceApi:
        ids: ClassVar[list[str]] = ["trace-a"]
        fail = False

        def list(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["fields"] == "core"
            if self.fail:
                raise RuntimeError("private provider error")
            return SimpleNamespace(data=[SimpleNamespace(id=x) for x in self.ids], meta=SimpleNamespace(total_pages=1))

        def delete_multiple(self, **kwargs):
            calls.append(kwargs)

    api = FakeTraceApi()
    now = datetime.now(UTC)
    with factory() as session:
        job = PrivacyDeletionJob(
            id="receipt", user_id="owner", session_ids_json='["session-a"]', created_at=now - timedelta(hours=1)
        )
        session.add(job)
        session.commit()
        process_deletion_job(session, job, api, now=now)
        assert job.status == "verifying"  # API acceptance alone is insufficient.
        api.fail = True
        process_deletion_job(session, job, api, now=now + timedelta(minutes=16))
        assert job.error_code == "langfuse_cleanup_failed"
        assert job.user_id == "owner"
        api.fail = False
        api.ids = []
        process_deletion_job(session, job, api, now=now + timedelta(minutes=32))
        assert job.status == "linked_traces_deleted"
        assert job.user_id is None
        assert job.session_ids_json == "[]"
    assert any(x.get("session_id") == "session-a" for x in calls)
    assert any(str(x.get("user_id", "")).startswith("anon_user_") for x in calls)
    assert any(str(x.get("session_id", "")).startswith("anon_session_") for x in calls)


def test_langfuse_v4_deletion_client_can_be_constructed(privacy_env, monkeypatch):
    from psych_support_bot.infra.telemetry import tracing

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    get_settings.cache_clear()
    api = _trace_api()
    assert api is not None
    assert callable(api.list)
    assert callable(api.delete_multiple)
    tracing._langfuse_client.shutdown()
    tracing._langfuse_client = None
