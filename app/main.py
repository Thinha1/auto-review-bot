"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import text

from app.api.dashboard import router as dashboard_router
from app.api.github_webhook import router as github_webhook_router
from app.config import Settings, get_settings
from app.github.oauth import GitHubOAuthClient
from app.logging import configure_logging
from app.metrics import metrics
from app.storage.database import create_engine, make_session_factory

APP_NAME = "PR Review Agent"


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings is not None:
            app.state.settings = settings
        else:
            try:
                app.state.settings = get_settings()
            except ValidationError as exc:
                fields = ["".join(str(part) for part in err["loc"]) for err in exc.errors()]
                raise RuntimeError(f"Invalid or missing settings for: {', '.join(fields)}") from exc
        engine = create_engine(app.state.settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        if app.state.settings.github_client_id and app.state.settings.github_client_secret:
            app.state.oauth_client = GitHubOAuthClient(
                app.state.settings.github_client_id,
                app.state.settings.github_client_secret,
                api_url=app.state.settings.github_api_url,
            )
        configure_logging()
        yield
        engine.dispose()

    app = FastAPI(title=APP_NAME, lifespan=lifespan)
    app.include_router(github_webhook_router)
    app.include_router(dashboard_router)
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent.parent / "static")),
        name="static",
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(request: Request) -> dict[str, str]:
        with request.app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def prometheus_metrics() -> str:
        return metrics.render()

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/dashboard")

    return app


app = create_app()
