"""User-owned profile-memory pause and erasure boundaries."""

from uuid import uuid4

from fastapi.testclient import TestClient

from psych_support_bot.ai.profile.extractor import record_turn_interventions, run_turn_extraction
from psych_support_bot.ai.profile.panel import build_profile_panel
from psych_support_bot.ai.profile.renderer import render_profile_block
from psych_support_bot.app import app
from psych_support_bot.infra.db.models import (
    ProfileBelief,
    ProfileExtractionStats,
    ProfileInterventionEvent,
    ProfileMemoryPreference,
    RiskEvent,
    UserTimeProfile,
)
from psych_support_bot.infra.db.profile_repositories import (
    get_belief,
    is_profile_memory_enabled,
    record_claim,
    record_extraction_stats,
    set_profile_memory_enabled,
)
from psych_support_bot.infra.db.repositories import build_memory_snapshot, upsert_user_profile
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.services.time_profile import get_user_time_profile


def _uid() -> str:
    return "profile-pref-" + uuid4().hex[:10]


def test_missing_preference_preserves_existing_enabled_behavior() -> None:
    user_id = _uid()
    with SessionLocal() as session:
        assert is_profile_memory_enabled(session, user_id) is True
        panel = build_profile_panel(session, user_id)
        pm = panel["profile_memory"]
        assert pm["enabled"] is True
        assert pm["has_data"] is False


def test_disabled_profile_memory_blocks_collection_use_and_time_profile() -> None:
    user_id = _uid()
    with SessionLocal() as session:
        record_claim(session, user_id, dimension="D1", key="sleep", claim_text="x", confidence=0.9)
        upsert_user_profile(session, user_id, "", "", "", "请用简短的方式回应", "")
        session.commit()
        set_profile_memory_enabled(session, user_id, False)
        session.commit()

        before_stats = session.query(ProfileExtractionStats).filter_by(user_id=user_id).count()
        run_turn_extraction(
            session,
            user_id=user_id,
            session_id="s1",
            topics=["work_stress"],
            risk_level="low",
            exercise_tag=None,
            valence_text="最近工作压力很大",
        )
        record_turn_interventions(
            session,
            user_id=user_id,
            session_id="s1",
            practice_action="offer",
            exercise_tag=None,
            question_candidates=["睡眠"],
            no_question_mode=False,
            mode="support",
            risk_level="low",
        )
        time_profile = get_user_time_profile(session, user_id)
        session.commit()

        assert render_profile_block(session, user_id, user_message="最近睡不好") is None
        assert "请用简短的方式回应" not in build_memory_snapshot(session, user_id)
        assert build_profile_panel(session, user_id)["sections"] == []
        assert get_belief(session, user_id, "sleep") is not None
        assert get_belief(session, user_id, "work_stress") is None
        assert session.query(ProfileExtractionStats).filter_by(user_id=user_id).count() == before_stats
        assert session.query(ProfileInterventionEvent).filter_by(user_id=user_id).count() == 0
        assert session.get(UserTimeProfile, user_id) is None
        assert time_profile["frequency_tier"] == "disabled"


def test_api_pause_then_confirmed_clear_preserves_safety_records() -> None:
    user_id = _uid()
    with SessionLocal() as session:
        record_claim(session, user_id, dimension="D1", key="sleep", claim_text="x", confidence=0.9)
        record_extraction_stats(session, user_id, session_id="s1", trigger="test", model="test")
        session.add(
            ProfileInterventionEvent(
                user_id=user_id,
                session_id="s1",
                intervention_kind="practice_offer",
                detail_json="{}",
            )
        )
        session.add(UserTimeProfile(user_id=user_id))
        session.add(
            RiskEvent(
                user_id=user_id,
                session_id="s1",
                risk_level="high",
                risk_reason="test fixture",
            )
        )
        session.commit()

    client = TestClient(app)
    assert client.get("/v1/me/profile-memory", params={"user_id": user_id}).json() == {
        "enabled": True,
        "has_data": True,
        "updated_at": None,
    }
    paused = client.put("/v1/me/profile-memory", json={"user_id": user_id, "enabled": False})
    assert paused.status_code == 200
    assert paused.json()["enabled"] is False
    assert paused.json()["has_data"] is True

    token = client.post(
        "/v1/me/confirm-intent",
        json={"user_id": user_id, "action": "clear_profile_memory"},
    ).json()["confirm_token"]
    cleared = client.delete(
        "/v1/me/profile-memory",
        params={"user_id": user_id, "confirm_token": token},
    )
    assert cleared.status_code == 200
    assert cleared.json()["enabled"] is False
    assert cleared.json()["deleted"]["profile_beliefs"] == 1
    assert cleared.json()["deleted"]["user_time_profiles"] == 1

    with SessionLocal() as session:
        preference = session.get(ProfileMemoryPreference, user_id)
        assert preference is not None and preference.enabled is False
        assert session.query(ProfileBelief).filter_by(user_id=user_id).count() == 0
        assert session.query(ProfileExtractionStats).filter_by(user_id=user_id).count() == 0
        assert session.query(ProfileInterventionEvent).filter_by(user_id=user_id).count() == 0
        assert session.get(UserTimeProfile, user_id) is None
        assert session.query(RiskEvent).filter_by(user_id=user_id).count() == 1
