import logging
from datetime import datetime, timezone

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.middleware import LoggingMiddleware
from app.db.session import async_session_maker
from app.routers import auth

setup_logging(settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="MasjidKoi Backend API — connecting worshippers with their nearest masjid.",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(LoggingMiddleware)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get(
    "/health",
    tags=["health"],
    summary="Service health check",
)
async def health() -> JSONResponse:
    db_status = "ok"
    postgis_version: str | None = None
    db_error: str | None = None

    try:
        async with async_session_maker() as session:
            result = await session.execute(text("SELECT PostGIS_Version()"))
            postgis_version = result.scalar_one()
    except Exception as exc:
        db_status = "error"
        db_error = str(exc)
        logger.error("Health check DB error", extra={"error": db_error})

    http_status = (
        status.HTTP_200_OK
        if db_status == "ok"
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )

    return JSONResponse(
        status_code=http_status,
        content={
            "status": "ok" if db_status == "ok" else "degraded",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": settings.VERSION,
            "environment": settings.APP_ENV,
            "checks": {
                "api": "ok",
                "database": db_status,
                "postgis": postgis_version,
                **({"error": db_error} if db_error else {}),
            },
        },
    )
