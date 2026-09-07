import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from psych_support_bot.api.routes.analytics import router as analytics_router
from psych_support_bot.api.routes.assessments import router as assessments_router
from psych_support_bot.api.routes.auth import router as auth_router
from psych_support_bot.api.routes.checkins import router as checkins_router
from psych_support_bot.api.routes.conversation import router as conversation_router
from psych_support_bot.api.routes.exercises import router as exercises_router
from psych_support_bot.api.routes.health import router as health_router
from psych_support_bot.api.routes.me import router as me_router
from psych_support_bot.api.routes.plans import router as plans_router
from psych_support_bot.api.routes.reports import router as reports_router
from psych_support_bot.api.routes.system import router as system_router
from psych_support_bot.api.routes.users import router as users_router
from psych_support_bot.infra.db.init_db import init_db
from psych_support_bot.infra.telemetry.tracing import flush_langfuse, get_langfuse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _run_migrations()
    init_db()
    get_langfuse()
    yield
    flush_langfuse()


def _run_migrations() -> None:
    """Alembic upgrade head at startup.

    init_db() 的 create_all 只能建新表，不能给已有表加列——线上 SQLite 已有
    assessments 等表，schema 演进必须走迁移。失败时 fail fast：schema 落后
    会让后续运行时查询崩溃，宁可启动失败也要显式暴露。
    """
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    from psych_support_bot.infra.config.settings import get_settings

    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))

    # 现存库由 create_all 建成、没有 alembic_version 表：直接 upgrade 会从
    # 零重放全部建表迁移并撞上 "table already exists"。先盖戳到引入迁移
    # 之前的最后一版 schema，再升级到 head。
    database_url = get_settings().database_url
    engine = sa.create_engine(
        database_url,
        connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
    )
    try:
        tables = set(sa.inspect(engine).get_table_names())
        if "alembic_version" not in tables and "users" in tables:
            command.stamp(cfg, "20260820_0001")
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    logger.info("Database migrations applied (alembic upgrade head)")


STATIC_DIR = Path(__file__).parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Psychological Support Bot API",
        version="0.1.0",
        description="Safety-first backend for a workflow-driven psychological support bot.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(system_router)
    app.include_router(auth_router)

    # JWT 认证守卫：数据端点统一挂载（api/auth.py）。AUTH_ENABLED=false 时
    # 守卫为 no-op（本地开发 / 既有测试），true 时无/坏 token 一律 401。
    # 注意只挂一次——重复 include 会先注册无守卫路由，守卫形同虚设。
    from fastapi import Depends

    from psych_support_bot.api.auth import require_auth

    data_router_guard = [Depends(require_auth)]
    for guarded in (
        conversation_router,
        assessments_router,
        checkins_router,
        plans_router,
        reports_router,
        users_router,
        analytics_router,
        exercises_router,
        me_router,
    ):
        app.include_router(guarded, dependencies=data_router_guard)

    @app.get("/")
    async def serve_index():
        return HTMLResponse(
            INDEX_FILE.read_text(encoding="utf-8"),
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def serve_favicon():
        return FileResponse(STATIC_DIR / "icons" / "favicon.ico", media_type="image/x-icon")

    return app


app = create_app()
