from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from psych_support_bot.api.routes.analytics import router as analytics_router
from psych_support_bot.api.routes.assessments import router as assessments_router
from psych_support_bot.api.routes.checkins import router as checkins_router
from psych_support_bot.api.routes.conversation import router as conversation_router
from psych_support_bot.api.routes.exercises import router as exercises_router
from psych_support_bot.api.routes.health import router as health_router
from psych_support_bot.api.routes.plans import router as plans_router
from psych_support_bot.api.routes.reports import router as reports_router
from psych_support_bot.api.routes.system import router as system_router
from psych_support_bot.api.routes.users import router as users_router
from psych_support_bot.infra.db.init_db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    _run_migrations()
    init_db()
    yield


def _run_migrations() -> None:
    from psych_support_bot.infra.db.migration_runner import run_migrations

    run_migrations()


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
    app.include_router(conversation_router)
    app.include_router(assessments_router)
    app.include_router(checkins_router)
    app.include_router(plans_router)
    app.include_router(reports_router)
    app.include_router(users_router)
    app.include_router(analytics_router)
    app.include_router(exercises_router)

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
    return app


app = create_app()
