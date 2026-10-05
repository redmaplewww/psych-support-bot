"""异步画像综合 Worker 全链路单测（不调真实 LLM）。

覆盖：
1. enqueue_evolution_job：入队、幂等、水位更新、禁用用户
2. process_pending_evolutions：pending 抢占、崩溃恢复、空队列
3. _process_single_job：完整 pipeline、失败重试、MAX_ATTEMPTS 退避
4. _load_evidence：belief + background 读取、distinct_sessions 计数
5. _validate：中英文禁止标签、空描述、反证标记、证据存在性
6. _compile_policy：受控枚举、默认值
7. _persist_snapshot：版本递增、旧快照 retired、shadow vs active
8. _formulate：LLM monkeypatch、JSON 解析、错误 fallback
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from psych_support_bot.infra.db.models import (
    ProfileEvolutionJob,
    ProfileSnapshot,
)
from psych_support_bot.infra.db.profile_repositories import record_claim
from psych_support_bot.infra.db.session import SessionLocal
from psych_support_bot.services.profile_evolution import (
    JOB_LEASE_SECONDS,
    MAX_ATTEMPTS,
    _compile_policy,
    _formulate,
    _load_evidence,
    _persist_snapshot,
    _process_single_job,
    _validate,
    enqueue_evolution_job,
    process_pending_evolutions,
)


def _uid() -> str:
    return f"pe-{uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _clean_evolution_tables():
    """每个测试前清理 evolution 相关表，避免 stale 数据干扰。"""
    with SessionLocal() as session:
        session.query(ProfileSnapshot).delete()
        session.query(ProfileEvolutionJob).delete()
        session.commit()
    yield


def _ensure_profile(session, user_id: str) -> None:
    """确保 UserProfile 存在（profile_evolution 依赖它）。"""
    from psych_support_bot.infra.db.repositories import upsert_user_profile

    upsert_user_profile(
        session,
        user_id,
        display_name="test",
        primary_concerns="",
        goals="",
        support_preferences="",
        risk_notes="",
    )


def _seed_beliefs(session, user_id: str, count: int = 3, *, session_id: str | None = None) -> None:
    """创建 N 条活跃 belief 用于测试。"""
    _ensure_profile(session, user_id)
    sid = session_id or f"s-{uuid4().hex[:8]}"
    keys = ["sleep", "anxiety", "rumination", "social_anxiety", "panic"]
    for i in range(count):
        record_claim(
            session,
            user_id,
            dimension="D1",
            key=keys[i % len(keys)],
            claim_text=f"claim-{i}",
            confidence=0.6 + i * 0.05,
            session_id=sid,
        )


# ── enqueue_evolution_job ────────────────────────────────────────────


class TestEnqueueEvolutionJob:
    def test_creates_pending_job(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            job = enqueue_evolution_job(session, uid)
            assert job is not None
            assert job.status == "pending"
            assert job.user_id == uid

    def test_returns_none_when_disabled(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            # 禁用画像
            from psych_support_bot.infra.db.models import ProfileMemoryPreference

            pref = ProfileMemoryPreference(user_id=uid, enabled=False)
            session.add(pref)
            session.commit()
            assert enqueue_evolution_job(session, uid) is None

    def test_returns_existing_pending_job(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            j1 = enqueue_evolution_job(session, uid)
            j2 = enqueue_evolution_job(session, uid)
            assert j1.id == j2.id  # 同一个任务

    def test_updates_watermark_on_existing_job(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid, count=2)
            j1 = enqueue_evolution_job(session, uid)
            wm1 = j1.evidence_watermark
            # 追加 belief 改变水位
            _seed_beliefs(session, uid, count=3)
            j2 = enqueue_evolution_job(session, uid)
            assert j2.id == j1.id
            assert j2.evidence_watermark != wm1  # 水位已更新

    def test_reuses_completed_job_when_watermark_unchanged(self) -> None:
        """水位未变时复用旧任务（避免重复处理 + 唯一约束冲突）。"""
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            j1 = enqueue_evolution_job(session, uid)
            j1.status = "completed"
            session.commit()
            j2 = enqueue_evolution_job(session, uid)
            assert j2.id == j1.id
            assert j2.status == "pending"  # 被重置为 pending


# ── process_pending_evolutions ───────────────────────────────────────


class TestProcessPendingEvolutions:
    def test_picks_up_pending_jobs(self, monkeypatch) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            enqueue_evolution_job(session, uid)

        # Mock LLM 避免真实调用
        monkeypatch.setattr(
            "psych_support_bot.infra.llm.generation.generate_profile_extraction",
            lambda **kwargs: '{"patterns": [], "how_to_be_with_them": "", "open_questions": [], "support_policy": {}}',
        )
        process_pending_evolutions()

        with SessionLocal() as session:
            job = session.query(ProfileEvolutionJob).filter_by(user_id=uid).first()
            assert job.status == "completed"

    def test_crash_recovery_resets_expired_running(self, monkeypatch) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            job = enqueue_evolution_job(session, uid)
            # 模拟崩溃：设为 running 且 started_at 过期
            job.status = "running"
            job.started_at = datetime.now(UTC) - timedelta(seconds=JOB_LEASE_SECONDS + 10)
            job.attempt_count = MAX_ATTEMPTS  # 防止 worker 重试后完成
            session.commit()

        # Mock LLM 抛异常，让 worker 处理失败（达到 MAX_ATTEMPTS → failed）
        monkeypatch.setattr(
            "psych_support_bot.infra.llm.generation.generate_profile_extraction",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("mock fail")),
        )
        process_pending_evolutions()

        with SessionLocal() as session:
            job = session.query(ProfileEvolutionJob).filter_by(user_id=uid).first()
            # crash recovery 重置为 pending → worker 抢占（attempt_count+1 > MAX_ATTEMPTS）→ failed
            assert job.status == "failed"

    def test_empty_queue_does_nothing(self) -> None:
        # 不应抛异常
        process_pending_evolutions()


# ── _process_single_job ──────────────────────────────────────────────


class TestProcessSingleJob:
    def test_full_pipeline_success(self, monkeypatch) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid, count=3, session_id="s-shared")
            enqueue_evolution_job(session, uid)
            job = session.query(ProfileEvolutionJob).filter_by(user_id=uid).first()

            monkeypatch.setattr(
                "psych_support_bot.infra.llm.generation.generate_profile_extraction",
                lambda **kwargs: json.dumps(
                    {
                        "patterns": [{"description": "tends to avoid social situations", "confidence": 0.7}],
                        "how_to_be_with_them": "gentle",
                        "open_questions": ["why?"],
                        "support_policy": {"response_length": "brief"},
                    }
                ),
            )

            _process_single_job(session, job)
            assert job.status == "completed"
            assert job.completed_at is not None

            # 验证快照已创建
            snap = session.query(ProfileSnapshot).filter_by(user_id=uid).first()
            assert snap is not None
            assert snap.version == 1

    def test_failure_retries_up_to_max(self, monkeypatch) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            enqueue_evolution_job(session, uid)
            job = session.query(ProfileEvolutionJob).filter_by(user_id=uid).first()
            # 原子抢占步骤会 +1，_process_single_job 时 attempt_count 已达 MAX_ATTEMPTS
            job.attempt_count = MAX_ATTEMPTS
            session.commit()

            monkeypatch.setattr(
                "psych_support_bot.services.profile_evolution._load_evidence",
                lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
            )

            _process_single_job(session, job)
            assert job.status == "failed"
            assert job.error_code == "RuntimeError"

    def test_failure_below_max_resets_to_pending(self, monkeypatch) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid)
            enqueue_evolution_job(session, uid)
            job = session.query(ProfileEvolutionJob).filter_by(user_id=uid).first()
            job.attempt_count = 1  # 未到上限
            session.commit()

            monkeypatch.setattr(
                "psych_support_bot.services.profile_evolution._load_evidence",
                lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
            )

            _process_single_job(session, job)
            assert job.status == "pending"


# ── _load_evidence ───────────────────────────────────────────────────


class TestLoadEvidence:
    def test_returns_beliefs_and_background(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid, count=3, session_id="s-shared")
            result = _load_evidence(session, uid)
            assert len(result["beliefs"]) == 3
            assert result["user_id"] == uid
            assert "distinct_sessions" in result

    def test_distinct_sessions_count(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            # 两条 belief 来自同一会话
            record_claim(session, uid, dimension="D1", key="sleep", claim_text="a", session_id="s-1")
            record_claim(session, uid, dimension="D1", key="anxiety", claim_text="b", session_id="s-1")
            result = _load_evidence(session, uid)
            assert result["distinct_sessions"] == 1

    def test_distinct_sessions_multiple(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            record_claim(session, uid, dimension="D1", key="sleep", claim_text="a", session_id="s-1")
            record_claim(session, uid, dimension="D1", key="anxiety", claim_text="b", session_id="s-2")
            result = _load_evidence(session, uid)
            assert result["distinct_sessions"] == 2

    def test_empty_user_returns_empty(self) -> None:
        with SessionLocal() as session:
            result = _load_evidence(session, "nonexistent")
            assert result["beliefs"] == []
            assert result["distinct_sessions"] == 0


# ── _validate ────────────────────────────────────────────────────────


class TestValidate:
    def test_forbidden_labels_english(self) -> None:
        candidate = {"patterns": [{"description": "shows narcissist tendencies", "confidence": 0.8}]}
        result = _validate(candidate)
        assert len(result["patterns"]) == 0

    def test_forbidden_labels_chinese(self) -> None:
        candidate = {"patterns": [{"description": "有自恋倾向", "confidence": 0.8}]}
        result = _validate(candidate)
        assert len(result["patterns"]) == 0

    def test_empty_description_filtered(self) -> None:
        candidate = {"patterns": [{"description": "  ", "confidence": 0.8}]}
        result = _validate(candidate)
        assert len(result["patterns"]) == 0

    def test_contradiction_forces_needs_verification(self) -> None:
        evidence = {
            "beliefs": [
                {
                    "key": "sleep",
                    "value": json.dumps({"clarification_status": "needs_clarification"}),
                }
            ]
        }
        candidate = {"patterns": [{"description": "trouble sleeping", "confidence": 0.7}]}
        result = _validate(candidate, evidence)
        assert result["patterns"][0]["needs_verification"] is True

    def test_single_session_forces_needs_verification(self) -> None:
        """单会话证据 → 强制 needs_verification。"""
        evidence = {"beliefs": [], "distinct_sessions": 1}
        candidate = {"patterns": [{"description": "tends to isolate", "confidence": 0.6}]}
        result = _validate(candidate, evidence)
        assert result["patterns"][0]["needs_verification"] is True

    def test_multi_session_allows_without_verification(self) -> None:
        """多会话证据 → 不强制 needs_verification。"""
        evidence = {
            "beliefs": [
                {"key": "sleep", "value": "{}", "origin_session_id": "s-1"},
                {"key": "anxiety", "value": "{}", "origin_session_id": "s-2"},
            ],
            "distinct_sessions": 2,
        }
        candidate = {"patterns": [{"description": "tends to isolate", "confidence": 0.8, "evidence_count": 3}]}
        result = _validate(candidate, evidence)
        assert result["patterns"][0].get("needs_verification") is not True

    def test_third_party_attribution_filtered(self) -> None:
        """第三方归属的 pattern 被丢弃。"""
        candidate = {
            "patterns": [
                {"description": "his friend causes him anxiety", "confidence": 0.7},
            ]
        }
        result = _validate(candidate)
        assert len(result["patterns"]) == 0

    def test_third_party_attribution_chinese(self) -> None:
        candidate = {
            "patterns": [
                {"description": "她的朋友让她很焦虑", "confidence": 0.7},
            ]
        }
        result = _validate(candidate)
        assert len(result["patterns"]) == 0

    def test_evidence_key_existence_check(self) -> None:
        evidence = {"beliefs": [{"key": "sleep", "value": "{}"}]}
        # 引用不存在的 key
        candidate = {"patterns": [{"description": "something", "confidence": 0.5, "evidence_keys": ["nonexistent"]}]}
        result = _validate(candidate, evidence)
        assert len(result["patterns"]) == 0

    def test_valid_pattern_passes(self) -> None:
        evidence = {"beliefs": [{"key": "sleep", "value": "{}"}], "distinct_sessions": 2}
        candidate = {
            "patterns": [
                {"description": "trouble sleeping", "confidence": 0.7, "evidence_keys": ["sleep"], "evidence_count": 3}
            ]
        }
        result = _validate(candidate, evidence)
        assert len(result["patterns"]) == 1

    def test_max_three_patterns(self) -> None:
        candidate = {
            "patterns": [{"description": f"pattern-{i}", "confidence": 0.5, "evidence_count": 3} for i in range(5)]
        }
        result = _validate(candidate)
        assert len(result["patterns"]) == 3


# ── _compile_policy ──────────────────────────────────────────────────


class TestCompilePolicy:
    def test_defaults_when_empty(self) -> None:
        policy = _compile_policy({})
        assert policy["response_length"] == "normal"
        assert policy["pacing"] == "validate_before_suggestions"

    def test_controls_valid_enums(self) -> None:
        candidate = {
            "support_policy": {
                "response_length": "brief",
                "pacing": "slow",
                "preferred_knowledge_paths": ["cbt", "mi"],
            }
        }
        policy = _compile_policy(candidate)
        assert policy["response_length"] == "brief"
        assert policy["preferred_knowledge_paths"] == ["cbt", "mi"]

    def test_rejects_invalid_enums(self) -> None:
        candidate = {
            "support_policy": {
                "response_length": "verbose",  # 不合法
                "preferred_knowledge_paths": ["cbt", "invalid_path"],
            }
        }
        policy = _compile_policy(candidate)
        assert policy["response_length"] == "normal"  # 回退默认
        assert policy["preferred_knowledge_paths"] == ["cbt"]


# ── _persist_snapshot ────────────────────────────────────────────────


class TestPersistSnapshot:
    def test_first_snapshot_version_1(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            snap = _persist_snapshot(session, uid, {"test": 1}, {}, "wm1")
            assert snap.version == 1
            assert snap.status == "active"

    def test_version_increments(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            _persist_snapshot(session, uid, {"v": 1}, {}, "wm1")
            snap2 = _persist_snapshot(session, uid, {"v": 2}, {}, "wm2")
            assert snap2.version == 2

    def test_old_snapshots_retired(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            s1 = _persist_snapshot(session, uid, {"v": 1}, {}, "wm1")
            _persist_snapshot(session, uid, {"v": 2}, {}, "wm2")
            session.refresh(s1)
            assert s1.status == "retired"

    def test_shadow_status(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            snap = _persist_snapshot(session, uid, {"test": 1}, {}, "wm1", shadow=True)
            assert snap.status == "shadow"

    def test_shadow_old_shadow_also_retired(self) -> None:
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            s1 = _persist_snapshot(session, uid, {"v": 1}, {}, "wm1", shadow=True)
            assert s1.status == "shadow"
            s2 = _persist_snapshot(session, uid, {"v": 2}, {}, "wm2", shadow=True)
            session.refresh(s1)
            assert s1.status == "retired"
            assert s2.status == "shadow"


# ── shadow lifecycle integration ────────────────────────────────────


class TestShadowLifecycle:
    def test_single_session_produces_shadow(self, monkeypatch) -> None:
        """单会话证据 → shadow 快照。"""
        uid = _uid()
        with SessionLocal() as session:
            _seed_beliefs(session, uid, count=3, session_id="s-only-one")
            enqueue_evolution_job(session, uid)
            job = session.query(ProfileEvolutionJob).filter_by(user_id=uid).first()

            monkeypatch.setattr(
                "psych_support_bot.infra.llm.generation.generate_profile_extraction",
                lambda **kwargs: (
                    '{"patterns": [], "how_to_be_with_them": "", "open_questions": [], "support_policy": {}}'
                ),
            )
            _process_single_job(session, job)

            snap = session.query(ProfileSnapshot).filter_by(user_id=uid).first()
            assert snap.status == "shadow"

    def test_multi_session_produces_active(self, monkeypatch) -> None:
        """多会话证据 → active 快照。"""
        uid = _uid()
        with SessionLocal() as session:
            _ensure_profile(session, uid)
            record_claim(session, uid, dimension="D1", key="sleep", claim_text="a", session_id="s-1")
            record_claim(session, uid, dimension="D1", key="anxiety", claim_text="b", session_id="s-2")
            record_claim(session, uid, dimension="D1", key="rumination", claim_text="c", session_id="s-2")
            enqueue_evolution_job(session, uid)
            job = session.query(ProfileEvolutionJob).filter_by(user_id=uid).first()

            monkeypatch.setattr(
                "psych_support_bot.infra.llm.generation.generate_profile_extraction",
                lambda **kwargs: (
                    '{"patterns": [], "how_to_be_with_them": "", "open_questions": [], "support_policy": {}}'
                ),
            )
            _process_single_job(session, job)

            snap = session.query(ProfileSnapshot).filter_by(user_id=uid).first()
            assert snap.status == "active"

    def test_shadow_auto_promotes_on_multi_session_evidence(self, monkeypatch) -> None:
        """shadow 快照在新会话证据到达时自动升级为 active。"""
        uid = _uid()

        # 第一个会话：单会话 → shadow
        with SessionLocal() as session:
            _seed_beliefs(session, uid, count=2, session_id="s-first")
            enqueue_evolution_job(session, uid)
            job = session.query(ProfileEvolutionJob).filter_by(user_id=uid).first()

            monkeypatch.setattr(
                "psych_support_bot.infra.llm.generation.generate_profile_extraction",
                lambda **kwargs: (
                    '{"patterns": [], "how_to_be_with_them": "", "open_questions": [], "support_policy": {}}'
                ),
            )
            _process_single_job(session, job)

            snap = session.query(ProfileSnapshot).filter_by(user_id=uid).first()
            assert snap.status == "shadow"

        # 第二个会话：新 belief 到达，入队时应自动升级 shadow → active
        with SessionLocal() as session:
            record_claim(session, uid, dimension="D1", key="social_anxiety", claim_text="new", session_id="s-second")
            enqueue_evolution_job(session, uid)

            snap = session.query(ProfileSnapshot).filter_by(user_id=uid).first()
            assert snap.status == "active"  # 已升级


# ── _formulate ───────────────────────────────────────────────────────


class TestFormulate:
    def test_parses_valid_json(self, monkeypatch) -> None:
        uid = _uid()
        monkeypatch.setattr(
            "psych_support_bot.infra.llm.generation.generate_profile_extraction",
            lambda **kwargs: (
                '{"patterns": [], "how_to_be_with_them": "gentle", "open_questions": [], "support_policy": {}}'
            ),
        )
        with SessionLocal() as session:
            result = _formulate(session, uid, {"beliefs": [], "background": {}, "user_id": uid})
            assert result["how_to_be_with_them"] == "gentle"

    def test_handles_markdown_fence(self, monkeypatch) -> None:
        uid = _uid()
        monkeypatch.setattr(
            "psych_support_bot.infra.llm.generation.generate_profile_extraction",
            lambda **kwargs: (
                '```json\n{"patterns": [], "how_to_be_with_them": "", "open_questions": [], "support_policy": {}}\n```'
            ),
        )
        with SessionLocal() as session:
            result = _formulate(session, uid, {"beliefs": [], "background": {}, "user_id": uid})
            assert "patterns" in result

    def test_fallback_on_invalid_json(self, monkeypatch) -> None:
        uid = _uid()
        monkeypatch.setattr(
            "psych_support_bot.infra.llm.generation.generate_profile_extraction",
            lambda **kwargs: "not json at all",
        )
        with SessionLocal() as session:
            result = _formulate(session, uid, {"beliefs": [], "background": {}, "user_id": uid})
            assert result["patterns"] == []
