import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.db.session import async_session_maker
from app.repositories.announcement_repository import AnnouncementRepository
from app.services.announcement_service import notify_instant_followers

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


async def _run_singleton(
    job_id: str, ttl_s: int, job: Callable[[], Awaitable[None]]
) -> None:
    """Best-effort cross-process guard so only one worker/replica runs ``job``
    per tick — defence in depth behind ``SCHEDULER_ENABLED``.

    A Redis ``SET NX`` lock with a TTL shorter than the job's interval; it is
    deliberately NOT released on completion (so a same-tick duplicate can't sneak
    in) and auto-expires before the next tick. Fails OPEN — runs unguarded if
    Redis is down, matching the rate limiter — because skipping a sweep is worse
    than a rare duplicate."""
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        try:
            claimed = await redis.set(f"joblock:{job_id}", "1", nx=True, ex=ttl_s)
        except Exception as exc:
            logger.warning(
                "Job-lock unavailable for %s (running unguarded): %s", job_id, exc
            )
            claimed = True
        if not claimed:
            logger.info("Job %s already claimed this tick — skipping", job_id)
            return
        await job()
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass


async def publish_scheduled_announcements() -> None:
    """Auto-publish draft announcements whose scheduled_at has passed, then
    notify instant-mode followers of each (PRD 07, gap #15)."""

    async def _run() -> None:
        async with async_session_maker() as db:
            repo = AnnouncementRepository(db)
            overdue = await repo.get_overdue_scheduled()
            if not overdue:
                return
            now = datetime.now(timezone.utc)
            for ann in overdue:
                ann.is_published = True
                ann.published_at = now
                ann.scheduled_at = None
            await db.commit()
            logger.info("Auto-published %d scheduled announcement(s)", len(overdue))
            for ann in overdue:
                try:
                    await notify_instant_followers(db, ann)
                except Exception:
                    logger.exception(
                        "Instant fan-out failed for announcement %s",
                        ann.announcement_id,
                    )

    await _run_singleton("publish_scheduled_announcements", 50, _run)


async def send_daily_digests() -> None:
    """Hourly digest bucketing job — serves users whose digest hour matches the
    current Asia/Dhaka hour (PRD 07, gap #16)."""

    async def _run() -> None:
        async with async_session_maker() as db:
            from app.services.digest_service import DigestService

            await DigestService(db).run_due()

    await _run_singleton("send_daily_digests", 600, _run)


async def send_recurring_donation_nudges() -> None:
    """Fire one push per due recurring-donation schedule and advance it to the
    next cycle, collapsing any missed cycles (PRD 05). Never auto-charges."""

    async def _run() -> None:
        async with async_session_maker() as db:
            from app.services.recurring_schedule_service import RecurringScheduleService

            await RecurringScheduleService(db).run_due_nudges()

    await _run_singleton("send_recurring_donation_nudges", 600, _run)


async def sweep_stale_pending_donations() -> None:
    """Expire donations stuck PENDING > 24h to FAILED and send one recovery push
    each (PRD 05). The FAILED status itself ensures the push never repeats."""

    async def _run() -> None:
        async with async_session_maker() as db:
            from app.services.donation_service import DonationService

            await DonationService(db).sweep_stale_pending()

    await _run_singleton("sweep_stale_pending_donations", 600, _run)


async def reap_push_receipts() -> None:
    """Poll Expo delivery receipts ~15 min after send and prune tokens it reports
    DeviceNotRegistered — failures that only surface asynchronously (PRD 03 #0
    follow-up). A no-op unless PUSH_ENABLED: LoggingTransport returns no receipts,
    so nothing is ever recorded to poll."""

    async def _run() -> None:
        async with async_session_maker() as db:
            from app.services.push_service import PushService

            reaped = await PushService(db).reap_due_receipts()
            if reaped:
                logger.info("Reaped %d dead device token(s) from push receipts", reaped)

    await _run_singleton("reap_push_receipts", 500, _run)
