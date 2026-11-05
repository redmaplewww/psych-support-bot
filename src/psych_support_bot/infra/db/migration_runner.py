"""Run the same packaged migrations from a checkout, container or installed wheel."""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from psych_support_bot.infra.config.settings import get_settings

LEGACY_TABLES = {
    "users",
    "user_profiles",
    "sessions",
    "messages",
    "assessments",
    "checkins",
    "risk_events",
    "weekly_reports",
}
ASSESSMENT_COLUMNS = {
    "plain_meaning",
    "functional_impact",
    "care_consideration",
    "disclaimer",
    "needs_safety_followup",
}


def run_migrations(database_url: str | None = None) -> None:
    url = database_url or get_settings().database_url
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    engine = sa.create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )
    try:
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            inspector = sa.inspect(connection)
            tables = set(inspector.get_table_names())
            if "alembic_version" not in tables and tables:
                if not LEGACY_TABLES.issubset(tables):
                    raise RuntimeError("Unversioned database does not match the legacy schema; refusing to stamp it.")
                columns = {column["name"] for column in inspector.get_columns("assessments")}
                extra = columns & ASSESSMENT_COLUMNS
                if extra == ASSESSMENT_COLUMNS and "questionnaire_sessions" in tables:
                    legacy_revision = "20260820_0001"
                elif not extra and "questionnaire_sessions" not in tables:
                    legacy_revision = "20260410_0002"
                else:
                    raise RuntimeError("Partially upgraded legacy schema; reconcile it before running migrations.")
                command.stamp(cfg, legacy_revision)
            command.upgrade(cfg, "head")
    finally:
        engine.dispose()
