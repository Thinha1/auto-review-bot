"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.logging import configure_logging

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
        configure_logging()
        yield

    app = FastAPI(title=APP_NAME, lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
