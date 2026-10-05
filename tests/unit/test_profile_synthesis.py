"""K3 综合引擎单测（不调真实 LLM）。

覆盖：
1. run_k3_synthesis：正常流程、belief 不足提前返回、LLM 不可用、JSON 解析
2. render_understanding：正常渲染、空 understanding、语言切换
3. 格式化辅助函数
"""

from __future__ import annotations

import json
from uuid import uuid4

from psych_support_bot.ai.profile.synthesis import (
    _format_beliefs_for_k3,
    _format_checkin_trend,
    _format_slice_summaries,
    render_understanding,
    run_k3_synthesis,
)
from psych_support_bot.infra.db.models import ProfileBelief, SliceSummary
from psych_support_bot.infra.db.profile_repositories import record_claim
from psych_support_bot.infra.db.repositories import get_user_profile, upsert_user_profile
from psych_support_bot.infra.db.session import SessionLocal


def _uid() -> str:
    return f"k3-{uuid4().hex[:8]}"


def _ensure_profile(session, user_id: str) -> None:
    upsert_user_profile(
        session,
        user_id,
        display_name="test",
        primary_concerns="",
        goals="",
        support_preferences="",
        risk_notes="",
    )


def _seed_beliefs(session, user_id: str, count: int = 3, *, session_id: str = "s-1") -> None:
    _ensure_profile(session, user_id)
    keys = ["sleep", "anxiety", "rumination", "social_anxiety", "panic"]
    for i in range(count):
        record_claim(
            session,
            user_id,
            dimension="D1",
            key=keys[i % len(keys)],
            claim_text=f"claim-{i}",
            confidence=0.6 + i * 0.05,
            session_id=session_id,
        )


# ── 格式化辅助函数 ───────────────────────────────────────────────────


class TestFormatBeliefs:
    def test_formats_beliefs(self) -> None:
        beliefs = [
            ProfileBelief(dimension="D1", key="sleep", claim_text="睡不好", confidence=0.8, layer="L2"),
            ProfileBelief(dimension="D3", key="avoidance", claim_text="回避社交", confidence=0.6, layer="L4"),
        ]
        result = _format_beliefs_for_k3(beliefs)
        assert "D1" in result and "sleep" in result
        assert "D3" in result and "avoidance" in result

    def test_empty_beliefs(self) -> None:
        assert _format_beliefs_for_k3([]) == ""


class TestFormatSliceSummaries:
    def test_formats_summaries(self) -> None:
        from datetime import UTC, datetime

        s = SliceSummary(
            slice_id="sl-1",
            user_id="u1",
            summary_text="讨论了焦虑",
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        result = _format_slice_summaries([s])
        assert "讨论了焦虑" in result

    def test_empty_summaries(self) -> None:
        assert _format_slice_summaries([]) == ""


class TestFormatCheckinTrend:
    def test_formats_checkins(self) -> None:
        class FakeCheckin:
            checkin_date = "2025-01-01"
            mood_score = 6
            anxiety_score = 4
            sleep_hours = 7.0

        result = _format_checkin_trend([FakeCheckin()])
        assert "mood=6" in result
        assert "anxiety=4" in result

    def test_empty_checkins(self) -> None:
        assert _format_checkin_trend([]) == ""


# ── run_k3_synthesis ─────────────────────────────────────────────────


class TestRunK3Synthesis:
    def test_returns_none_when_disabled(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            from psych_support_bot.infra.db.models import ProfileMemoryPreference

            session.add(ProfileMemoryPreference(user_id=uid, enabled=False))
            session.commit()
            assert run_k3_synthesis(session, uid) is None

    def test_returns_none_with_fewer_than_two_beliefs(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            record_claim(session, uid, dimension="D1", key="sleep", claim_text="a", session_id="s-1")
            assert run_k3_synthesis(session, uid) is None

    def test_writes_understanding_json(self, monkeypatch) -> None:
        uid = _uid()
        canned = json.dumps(
            {
                "patterns": [{"description": "tends to avoid conflict", "confidence": 0.7}],
                "how_to_be_with_them": "gentle and patient",
                "open_questions": ["why conflict?"],
                "what_works": ["validation"],
            }
        )
        monkeypatch.setattr(
            "psych_support_bot.infra.llm.generation.generate_profile_extraction",
            lambda **kwargs: canned,
        )
        with SessionLocal() as session:
            _seed_beliefs(session, uid, count=3)
            result = run_k3_synthesis(session, uid)
            assert result is not None
            assert result["how_to_be_with_them"] == "gentle and patient"

            # 验证写入了 UserProfile
            profile = get_user_profile(session, uid)
            assert profile is not None
            stored = json.loads(profile.understanding_json)
            assert stored["how_to_be_with_them"] == "gentle and patient"

    def test_handles_markdown_fence(self, monkeypatch) -> None:
        uid = _uid()
        monkeypatch.setattr(
            "psych_support_bot.infra.llm.generation.generate_profile_extraction",
            lambda **kwargs: (
                '```json\n{"patterns": [], "how_to_be_with_them": "", "open_questions": [], "what_works": []}\n```'
            ),
        )
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            result = run_k3_synthesis(session, uid)
            assert result is not None
            assert "patterns" in result

    def test_returns_none_on_invalid_json(self, monkeypatch) -> None:
        uid = _uid()
        monkeypatch.setattr(
            "psych_support_bot.infra.llm.generation.generate_profile_extraction",
            lambda **kwargs: "not json",
        )
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            result = run_k3_synthesis(session, uid)
            assert result is None

    def test_returns_none_on_llm_error(self, monkeypatch) -> None:
        uid = _uid()

        def _boom(**kwargs):
            from psych_support_bot.infra.llm.generation import LLMUnavailableError

            raise LLMUnavailableError("down")

        monkeypatch.setattr(
            "psych_support_bot.infra.llm.generation.generate_profile_extraction",
            _boom,
        )
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            result = run_k3_synthesis(session, uid)
            assert result is None

    def test_updates_existing_understanding(self, monkeypatch) -> None:
        uid = _uid()
        first = json.dumps(
            {
                "patterns": [{"description": "old pattern", "confidence": 0.5}],
                "how_to_be_with_them": "old style",
                "open_questions": [],
                "what_works": [],
            }
        )
        second = json.dumps(
            {
                "patterns": [{"description": "new pattern", "confidence": 0.8}],
                "how_to_be_with_them": "new style",
                "open_questions": [],
                "what_works": [],
            }
        )
        calls = []
        monkeypatch.setattr(
            "psych_support_bot.infra.llm.generation.generate_profile_extraction",
            lambda **kwargs: (calls.append(1), second if len(calls) > 1 else first)[-1],
        )
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            run_k3_synthesis(session, uid)
            run_k3_synthesis(session, uid)

            profile = get_user_profile(session, uid)
            stored = json.loads(profile.understanding_json)
            assert stored["how_to_be_with_them"] == "new style"


# ── render_understanding ─────────────────────────────────────────────


class TestRenderUnderstanding:
    def test_returns_none_when_no_understanding(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            assert render_understanding(session, uid) is None

    def test_renders_patterns_and_how_to(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            profile = get_user_profile(session, uid)
            profile.understanding_json = json.dumps(
                {
                    "patterns": [
                        {"description": "tends to ruminate at night"},
                        {"description": "avoids confrontation"},
                    ],
                    "how_to_be_with_them": "gentle",
                    "open_questions": ["why at night?"],
                }
            )
            session.commit()

            result = render_understanding(session, uid)
            assert result is not None
            assert "ruminate" in result
            assert "gentle" in result

    def test_limits_patterns_to_three(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            profile = get_user_profile(session, uid)
            profile.understanding_json = json.dumps(
                {
                    "patterns": [{"description": f"pattern-{i}"} for i in range(5)],
                    "how_to_be_with_them": "",
                    "open_questions": [],
                }
            )
            session.commit()

            result = render_understanding(session, uid)
            assert result is not None
            # 只渲染前 3 个
            assert "pattern-0" in result
            assert "pattern-3" not in result

    def test_handles_invalid_json_gracefully(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            profile = get_user_profile(session, uid)
            profile.understanding_json = "not json"
            session.commit()

            # 不应抛异常
            result = render_understanding(session, uid)
            assert result is None
