import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db.session import async_session_maker
from app.repositories.announcement_repository import AnnouncementRepository
from app.services.announcement_service import notify_instant_followers

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


async def publish_scheduled_announcements() -> None:
    """Auto-publish draft announcements whose scheduled_at has passed, then
    notify instant-mode followers of each (PRD 07, gap #15)."""
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


async def send_daily_digests() -> None:
    """Hourly digest bucketing job — serves users whose digest hour matches the
    current Asia/Dhaka hour (PRD 07, gap #16)."""
    async with async_session_maker() as db:
        from app.services.digest_service import DigestService

        await DigestService(db).run_due()
