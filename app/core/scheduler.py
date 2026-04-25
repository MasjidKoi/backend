import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db.session import async_session_maker
from app.repositories.announcement_repository import AnnouncementRepository

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


async def publish_scheduled_announcements() -> None:
    """Auto-publish draft announcements whose scheduled_at has passed."""
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
