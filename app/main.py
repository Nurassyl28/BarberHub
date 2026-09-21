"""Application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, register_request_logging
from app.db.session import engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    configure_logging(settings.LOG_LEVEL)

    app = FastAPI(
        title=settings.PROJECT_NAME,
        description="Booking platform for barbershops.",
        version="0.1.0",
        debug=settings.DEBUG,
        openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    if settings.CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_request_logging(app)
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    _mount_frontend(app)

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "environment": settings.ENVIRONMENT, "version": "0.1.0"}

    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the client from the API process.

    One command and one origin for the whole thing: no CORS to configure, no
    second server to remember to start. The directory is optional so a
    deployment that only wants the API can simply not ship it.
    """
    directory = Path(__file__).resolve().parent.parent / "frontend"
    if not directory.is_dir():
        return

    app.mount("/app", StaticFiles(directory=directory, html=True), name="frontend")

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:
        return RedirectResponse("/app/")


app = create_app()
