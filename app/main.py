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
from app.core.scheduler import (
    publish_scheduled_announcements,
    purge_deleted_accounts,
    reap_push_receipts,
    scheduler,
    send_daily_digests,
    send_recurring_donation_nudges,
    sweep_stale_pending_donations,
)
from app.db.session import async_session_maker
from app.routers import (
    admin,
    announcements,
    auth,
    campaigns,
    co_admins,
    community_photos,
    config,
    donations,
    email_templates,
    events,
    feed,
    gamification,
    goals,
    masjid_questions,
    masjid_submissions,
    masjids,
    payments,
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

    # Scheduler runs on exactly one instance (see settings.SCHEDULER_ENABLED).
    # Jobs additionally self-guard with a Redis lock, so an accidental second
    # runner won't duplicate fan-out.
    if settings.SCHEDULER_ENABLED:
        scheduler.add_job(
            publish_scheduled_announcements,
            trigger="interval",
            minutes=1,
            id="publish_scheduled_announcements",
            replace_existing=True,
        )
        # Digest job runs at the top of every hour; each run serves the bucket of
        # users whose chosen digest hour matches the current Asia/Dhaka hour.
        scheduler.add_job(
            send_daily_digests,
            trigger="cron",
            minute=0,
            id="send_daily_digests",
            replace_existing=True,
        )
        # Recurring-donation nudges: every 15 minutes, fire pushes for schedules
        # whose next_due_at has passed (PRD 05).
        scheduler.add_job(
            send_recurring_donation_nudges,
            trigger="interval",
            minutes=15,
            id="send_recurring_donation_nudges",
            replace_existing=True,
        )
        # Stale-pending sweep: hourly, expire abandoned PENDING donations to FAILED
        # and send one recovery push each (PRD 05).
        scheduler.add_job(
            sweep_stale_pending_donations,
            trigger="interval",
            hours=1,
            id="sweep_stale_pending_donations",
            replace_existing=True,
        )
        # Push-receipt reaper: every 10 min, poll Expo for receipts on sends
        # older than ~15 min and prune tokens reported DeviceNotRegistered
        # (PRD 03 #0 follow-up). No-op unless PUSH_ENABLED.
        scheduler.add_job(
            reap_push_receipts,
            trigger="interval",
            minutes=10,
            id="reap_push_receipts",
            replace_existing=True,
        )
        # Account-deletion purge: daily at 03:30 UTC, anonymise soft-deleted
        # accounts past the 30-day window (PRD 09 #1). The window is in days, so a
        # daily tick is fine; the per-account purged_at stamp keeps it idempotent.
        scheduler.add_job(
            purge_deleted_accounts,
            trigger="cron",
            hour=3,
            minute=30,
            id="purge_deleted_accounts",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("APScheduler started")
    else:
        logger.info("Scheduler disabled on this instance (SCHEDULER_ENABLED=false)")

    yield

    if settings.SCHEDULER_ENABLED:
        scheduler.shutdown(wait=False)
    await app.state.redis.aclose()
    logger.info("Scheduler and Redis stopped")


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
# Before masjids.router so /masjids/submissions matches ahead of /masjids/{masjid_id}.
app.include_router(masjid_submissions.router)
# community_photos paths carry a static segment so they never collide with
# masjids.router's /masjids/{masjid_id}; ordering relative to it is immaterial.
app.include_router(community_photos.router)
# Q&A paths carry a static `/questions` segment so they never collide with
# masjids.router's /masjids/{masjid_id}; ordering relative to it is immaterial.
app.include_router(masjid_questions.router)
app.include_router(masjids.router)
app.include_router(prayer_times.router)
app.include_router(announcements.router)
app.include_router(events.router)
app.include_router(campaigns.router)
app.include_router(co_admins.router)
app.include_router(gamification.masjid_router)
app.include_router(gamification.user_router)
app.include_router(goals.router)
app.include_router(support.user_router)
app.include_router(support.admin_router)
app.include_router(feed.router)
app.include_router(users.router)
app.include_router(admin.router)
# Public, unauthenticated app config (static /app-config segment — no collision).
app.include_router(config.router)
# Donations: user dashboard + admin views, and the unauthenticated SSLCommerz
# IPN/redirect callbacks. Static path segments (/donations, /payments) keep these
# from colliding with /masjids/{masjid_id} and /admin/* dynamic routes.
app.include_router(donations.user_router)
app.include_router(donations.admin_router)
app.include_router(payments.router)
app.include_router(email_templates.router)


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
