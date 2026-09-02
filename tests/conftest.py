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
    # 投机并行走真实回复生成 LLM——测试环境整体关闭（个别投机用例自行开启），
    # 否则既有 risk/回单类单测会随投机路径多打一次真实 LLM。
    os.environ["SPECULATIVE_REPLY_ENABLED"] = "false"
    # 与应用启动（app._run_migrations）保持一致：测试库也走 Alembic 迁移，
    # 否则旧 schema 的 sqlite 文件缺新列会让涉及新列的用例全部失败。
    from psych_support_bot.app import _run_migrations

    _run_migrations()


@pytest.fixture(autouse=True)
def _no_langfuse_export(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    # Drop cached settings/singleton created before (or outside) this fixture.
    monkeypatch.setattr("psych_support_bot.infra.telemetry.tracing._langfuse_client", None)
    get_settings.cache_clear()

    yield

    from psych_support_bot.infra.telemetry import tracing

    tracing._langfuse_client = None
