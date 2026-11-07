"""FastAPI application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import auth, history, monitors
from app.core.config import get_settings
from app.core.database import dispose_engine, get_session_factory
from app.core.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging at startup and dispose the engine at shutdown."""
    setup_logging(get_settings().log_level)
    logger.info("application_started")
    yield
    await dispose_engine()
    logger.info("application_stopped")


def create_app() -> FastAPI:
    """Build the application instance."""
    settings = get_settings()
    app = FastAPI(
        title="PulseWatch",
        version="0.1.0",
        description="Website uptime monitoring with incidents and notifications.",
        debug=settings.debug,
        lifespan=lifespan,
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail, "code": f"http_{exc.status_code}"},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(part) for part in error.get("loc", [])),
                "message": error.get("msg", "invalid value"),
            }
            for error in exc.errors()
        ]
        first = errors[0]["message"] if errors else "invalid request"
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": first, "code": "validation_error", "errors": errors},
        )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    @app.get("/health/ready", tags=["system"])
    async def readiness() -> JSONResponse:
        """Readiness probe that verifies the database connection."""
        try:
            async with get_session_factory()() as session:
                await session.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            logger.exception("readiness_check_failed")
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(content={"status": "ready"})

    app.include_router(auth.router, prefix="/api")
    app.include_router(monitors.router, prefix="/api")
    app.include_router(history.router, prefix="/api")
    return app


app = create_app()
