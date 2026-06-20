import uuid
from datetime import date, datetime, time

from sqlalchemy import func, select, tuple_

from app.models.announcement import Announcement
from app.models.masjid import Masjid
from app.models.masjid_event import EventRsvp, MasjidEvent
from app.models.user_masjid_follow import UserMasjidFollow
from app.repositories.base import BaseRepository


class FeedRepository(BaseRepository[Announcement]):
    """Aggregated reads across the caller's follows. One row per item, each
    carrying its masjid's id + display name so cards render without follow-up
    calls. Cursor pagination uses tuple comparison on the sort key for
    stability across pages (no offset drift when new items arrive)."""

    model = Announcement

    async def announcements_feed(
        self,
        user_id: uuid.UUID,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None,
    ) -> list[tuple[Announcement, str]]:
        """Published announcements from followed masjids, newest first. Fetches
        limit+1 so the caller can detect a further page."""
        stmt = (
            select(Announcement, Masjid.name.label("masjid_name"))
            .join(Masjid, Announcement.masjid_id == Masjid.masjid_id)
            .join(
                UserMasjidFollow,
                UserMasjidFollow.masjid_id == Announcement.masjid_id,
            )
            .where(
                UserMasjidFollow.user_id == user_id,
                Announcement.is_published == True,  # noqa: E712
            )
        )
        if cursor is not None:
            stmt = stmt.where(
                tuple_(Announcement.published_at, Announcement.announcement_id)
                < cursor
            )
        stmt = stmt.order_by(
            Announcement.published_at.desc(),
            Announcement.announcement_id.desc(),
        ).limit(limit + 1)
        rows = (await self.db.execute(stmt)).all()
        return [(r.Announcement, r.masjid_name) for r in rows]

    async def events_feed(
        self,
        user_id: uuid.UUID,
        today: date,
        limit: int,
        cursor: tuple[date, time, uuid.UUID] | None,
    ) -> list[tuple[MasjidEvent, str, int, bool]]:
        """Upcoming events from followed masjids, soonest first; past events
        excluded server-side. Embeds attendee count and the caller's RSVP
        state. Fetches limit+1 to detect a further page."""
        attendee_sq = (
            select(func.count())
            .where(EventRsvp.event_id == MasjidEvent.event_id)
            .correlate(MasjidEvent)
            .scalar_subquery()
        )
        rsvped_sq = (
            select(func.count())
            .where(
                EventRsvp.event_id == MasjidEvent.event_id,
                EventRsvp.user_id == user_id,
            )
            .correlate(MasjidEvent)
            .scalar_subquery()
        )
        stmt = (
            select(
                MasjidEvent,
                Masjid.name.label("masjid_name"),
                attendee_sq.label("attendee_count"),
                rsvped_sq.label("rsvped"),
            )
            .join(Masjid, MasjidEvent.masjid_id == Masjid.masjid_id)
            .join(
                UserMasjidFollow,
                UserMasjidFollow.masjid_id == MasjidEvent.masjid_id,
            )
            .where(
                UserMasjidFollow.user_id == user_id,
                MasjidEvent.event_date >= today,
            )
        )
        if cursor is not None:
            stmt = stmt.where(
                tuple_(
                    MasjidEvent.event_date,
                    MasjidEvent.event_time,
                    MasjidEvent.event_id,
                )
                > cursor
            )
        stmt = stmt.order_by(
            MasjidEvent.event_date.asc(),
            MasjidEvent.event_time.asc(),
            MasjidEvent.event_id.asc(),
        ).limit(limit + 1)
        rows = (await self.db.execute(stmt)).all()
        return [
            (r.MasjidEvent, r.masjid_name, r.attendee_count, bool(r.rsvped))
            for r in rows
        ]
