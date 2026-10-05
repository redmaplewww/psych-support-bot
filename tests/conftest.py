"""Shared pytest configuration.

1. Clear cached settings at session start (existing behaviour).
2. Isolate the suite from real Langfuse exports: the repository `.env`
   carries production Langfuse keys, and without this fixture every pytest
   run streamed synthetic node spans into the live project, polluting the
   traces used for reviewing real conversations.
"""

import os

import pytest

from psych_support_bot.infra.config.settings import get_settings


def pytest_sessionstart(session) -> None:  # type: ignore[no-untyped-def]
    get_settings.cache_clear()
    # Langfuse 流量标记为 test 环境（与 evals 的 "eval"、生产的
    # "production" 区分）；_no_langfuse_export fixture 本已屏蔽导出，
    # 这里是显式声明 + 双保险。
    os.environ.setdefault("LANGFUSE_ENVIRONMENT", "test")
    # 投机并行走真实回复生成 LLM——测试环境整体关闭（个别投机用例自行开启），
    # 否则既有 risk/回单类单测会随投机路径多打一次真实 LLM。
    os.environ["SPECULATIVE_REPLY_ENABLED"] = "false"
    # K2 画像语义提取同理默认关闭：练习轮等触发条件会在单测里打出真实
    # 提取调用。语义提取用例自行开启（monkeypatch settings）并 monkeypatch
    # generate_profile_extraction，不依赖真实供应商。
    os.environ["PROFILE_LLM_EXTRACTION_ENABLED"] = "false"
    # 与应用启动（app._run_migrations）保持一致：测试库也走 Alembic 迁移，
    # 否则旧 schema 的 sqlite 文件缺新列会让涉及新列的用例全部失败。
    from psych_support_bot.app import _run_migrations

    _run_migrations()


@pytest.fixture(autouse=True)
def _no_langfuse_export(monkeypatch):
    # Empty env overrides .env; deleting env vars would load production keys
    # straight back from .env on the next Settings construction.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")

    # Drop cached settings/singleton created before (or outside) this fixture.
    monkeypatch.setattr("psych_support_bot.infra.telemetry.tracing._langfuse_client", None)
    get_settings.cache_clear()

    yield

    from psych_support_bot.infra.telemetry import tracing

    tracing._langfuse_client = None


@pytest.fixture(autouse=True)
def _existing_feature_tests_assume_consent(request, monkeypatch):
    """Legacy feature fixtures predate the consent gate and test downstream logic.

    Privacy boundary tests use real guards and explicit acceptance instead.
    This override exists only in pytest, never as a runtime configuration flag.
    """
    if request.node.get_closest_marker("privacy_boundary"):
        yield
        return
    from psych_support_bot.api.privacy import require_privacy_consent
    from psych_support_bot.app import app

    monkeypatch.setitem(app.dependency_overrides, require_privacy_consent, lambda: None)
    monkeypatch.setattr("psych_support_bot.api.privacy.check_privacy_consent", lambda *_: None)
    yield


@pytest.fixture(autouse=True)
def _assume_profile_beta_consent(request, monkeypatch):
    """Legacy profile tests predate the beta consent gate.

    Tests marked with ``beta_consent_boundary`` opt out and test real guards.
    """
    if request.node.get_closest_marker("beta_consent_boundary"):
        yield
        return
    monkeypatch.setattr(
        "psych_support_bot.infra.db.profile_repositories.is_profile_beta_accepted",
        lambda *_a, **_kw: True,
    )
    monkeypatch.setattr(
        "psych_support_bot.infra.db.profile_repositories.is_sensitive_background_enabled",
        lambda *_a, **_kw: False,
    )
    yield


@pytest.fixture
def db_session():
    """Provide a database session for tests."""
    from psych_support_bot.infra.db.session import get_db_session

    session = next(get_db_session())
    yield session
    session.rollback()
    session.close()
