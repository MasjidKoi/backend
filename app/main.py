import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.middleware import LoggingMiddleware
from app.db.session import async_session_maker
from app.routers import (
    admin,
    announcements,
    auth,
    campaigns,
    co_admins,
    events,
    gamification,
    masjids,
    prayer_times,
    support,
    users,
)

setup_logging(settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    logger.info("Redis connection pool created")
    yield
    await app.state.redis.aclose()
    logger.info("Redis connection pool closed")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="MasjidKoi Backend API — connecting worshippers with their nearest masjid.",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS — allow frontend dev server and production origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(LoggingMiddleware)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(masjids.router)
app.include_router(prayer_times.router)
app.include_router(announcements.router)
app.include_router(events.router)
app.include_router(campaigns.router)
app.include_router(co_admins.router)
app.include_router(gamification.masjid_router)
app.include_router(gamification.user_router)
app.include_router(support.user_router)
app.include_router(support.admin_router)
app.include_router(users.router)
app.include_router(admin.router)


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
        status.HTTP_200_OK if db_status == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
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
