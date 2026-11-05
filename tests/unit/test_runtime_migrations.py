"""Exercise upgrade paths for databases created by the upstream main app."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from psych_support_bot.infra.db import models  # noqa: F401
from psych_support_bot.infra.db.base import Base
from psych_support_bot.infra.db.migration_runner import run_migrations


def test_fresh_database_and_repeat_startup(tmp_path):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    run_migrations(url)
    run_migrations(url)
    engine = sa.create_engine(url)
    try:
        assert "questionnaire_sessions" in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_main_create_all_database_keeps_existing_user(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = sa.create_engine(url)
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO users (id, created_at) VALUES ('legacy-user', '2026-01-01')"))
        run_migrations(url)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT id FROM users")).scalar_one() == "legacy-user"
    finally:
        engine.dispose()


def test_versioned_main_database_adds_questionnaire_fields(tmp_path):
    url = f"sqlite:///{tmp_path / 'versioned.db'}"
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parents[2] / "src/psych_support_bot/infra/db/migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "20260410_0002")
        run_migrations(url)
        columns = {column["name"] for column in sa.inspect(engine).get_columns("assessments")}
        assert "needs_safety_followup" in columns
        assert "questionnaire_sessions" in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_unknown_unversioned_database_is_not_stamped(tmp_path):
    url = f"sqlite:///{tmp_path / 'unknown.db'}"
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE users (id TEXT PRIMARY KEY)"))
        with pytest.raises(RuntimeError, match="refusing to stamp"):
            run_migrations(url)
        assert "alembic_version" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
