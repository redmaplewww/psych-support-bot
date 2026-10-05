"""Eight-turn scheduling comparison through the real extraction/storage path.

The model response is fixed to isolate scheduling from model sampling. This
measures dispatch counts and capture timing, not real-model extraction quality.
"""

import json
from uuid import uuid4

from psych_support_bot.ai.profile import semantic
from psych_support_bot.infra.config.settings import get_settings
from psych_support_bot.infra.db.models import ProfileExtractionStats
from psych_support_bot.infra.db.profile_repositories import get_belief, record_claim, set_profile_memory_enabled
from psych_support_bot.infra.db.session import SessionLocal

_REPEATED = "我焦虑睡不着"
_MECHANISM = "开会前我提前把要说的话在脑子里排练了好多遍，能不说话就不说话"
_TURNS = [_REPEATED, _MECHANISM, *([_REPEATED] * 6)]
_MECHANISM_KEY = "avoidance_maintenance.social"


def _run_scenario(monkeypatch, *, legacy: bool) -> tuple[list[int], int | None, int]:
    user_id = f"longitudinal-{uuid4().hex}"
    dispatch_turns = []
    captured_turn = None
    current_turn = 0

    def fixed_model(**kwargs):
        dispatch_turns.append(current_turn)
        text = kwargs["payload_text"].split("[User utterance this turn]\n", 1)[1]
        claims = []
        if text == _MECHANISM:
            claims.append({"dimension": "D3", "key": _MECHANISM_KEY, "claim_zh": "社交前反复排练", "confidence": 0.8})
        return json.dumps({"claims": claims}, ensure_ascii=False)

    with monkeypatch.context() as patch:
        patch.setattr(get_settings(), "profile_llm_extraction_enabled", True)
        patch.setattr(semantic, "generate_profile_extraction", fixed_model)
        if legacy:
            gate = semantic._should_llm_extract
            patch.setattr(
                semantic,
                "_should_llm_extract",
                lambda text, *, practice_event, turn_count, **_: gate(
                    text, practice_event=practice_event, turn_count=turn_count
                ),
            )
        with SessionLocal() as session:
            for key in ("sleep", "anxiety"):
                record_claim(session, user_id, dimension="D1", key=key, claim_text="已有主题", confidence=0.9)
            session.commit()
            for current_turn, text in enumerate(_TURNS, 1):
                semantic.run_semantic_extraction(
                    session,
                    user_id=user_id,
                    session_id=f"{user_id}-session",
                    user_text=text,
                    turn_count=current_turn,
                    risk_level="low",
                    practice_event=False,
                )
                belief = get_belief(session, user_id, _MECHANISM_KEY)
                if belief is not None and captured_turn is None:
                    captured_turn = current_turn
                    assert belief.layer == "L4"  # capture is not user confirmation
            stats_count = (
                session.query(ProfileExtractionStats)
                .filter(ProfileExtractionStats.user_id == user_id, ProfileExtractionStats.trigger == "llm_semantic")
                .count()
            )
    return dispatch_turns, captured_turn, stats_count


def test_eight_turns_reduce_dispatches_and_capture_new_mechanism_early(monkeypatch) -> None:
    legacy_turns, legacy_capture, legacy_stats = _run_scenario(monkeypatch, legacy=True)
    smart_turns, smart_capture, smart_stats = _run_scenario(monkeypatch, legacy=False)
    assert legacy_turns == [1, 3, 4, 5, 6, 7, 8]
    assert smart_turns == [2]
    assert (legacy_stats, smart_stats) == (7, 1)
    assert legacy_capture is None
    assert smart_capture == 2  # captured before the legacy third-turn cadence


def test_mechanism_signal_never_overrides_crisis_or_paused_memory(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(get_settings(), "profile_llm_extraction_enabled", True)
    monkeypatch.setattr(semantic, "generate_profile_extraction", lambda **kw: calls.append(kw))
    user_id = f"longitudinal-guard-{uuid4().hex}"
    with SessionLocal() as session:
        for risk_level in ("high", "critical"):
            semantic.run_semantic_extraction(
                session,
                user_id=user_id,
                session_id="guard",
                user_text=_MECHANISM,
                turn_count=2,
                risk_level=risk_level,
                practice_event=False,
            )
        set_profile_memory_enabled(session, user_id, False)
        session.commit()
        semantic.run_semantic_extraction(
            session,
            user_id=user_id,
            session_id="guard",
            user_text=_MECHANISM,
            turn_count=2,
            risk_level="low",
            practice_event=False,
        )
        assert get_belief(session, user_id, _MECHANISM_KEY) is None
        assert session.query(ProfileExtractionStats).filter(ProfileExtractionStats.user_id == user_id).count() == 0
    assert calls == []
