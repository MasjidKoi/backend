import base64
import binascii
import uuid
from datetime import date, datetime, time

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import DHAKA_TZ
from app.repositories.feed_repository import FeedRepository
from app.schemas.feed import (
    FeedAnnouncementItem,
    FeedAnnouncementsResponse,
    FeedEventItem,
    FeedEventsResponse,
)


def _encode_cursor(parts: list[str]) -> str:
    raw = "|".join(parts).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> list[str]:
    try:
        return base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor"
        ) from exc


class FeedService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = FeedRepository(db)

    async def announcements(
        self, user_id: uuid.UUID, limit: int, cursor: str | None
    ) -> FeedAnnouncementsResponse:
        decoded: tuple[datetime, uuid.UUID] | None = None
        if cursor:
            parts = _decode_cursor(cursor)
            if len(parts) != 2:
                raise HTTPException(status_code=400, detail="Invalid cursor")
            try:
                decoded = (datetime.fromisoformat(parts[0]), uuid.UUID(parts[1]))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid cursor") from exc

        rows = await self.repo.announcements_feed(user_id, limit, decoded)
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            FeedAnnouncementItem(
                announcement_id=a.announcement_id,
                masjid_id=a.masjid_id,
                masjid_name=name,
                title=a.title,
                body=a.body,
                published_at=a.published_at,
                created_at=a.created_at,
            )
            for a, name in rows
        ]
        next_cursor = None
        if has_more and rows:
            last, _ = rows[-1]
            next_cursor = _encode_cursor(
                [last.published_at.isoformat(), str(last.announcement_id)]
            )
        return FeedAnnouncementsResponse(items=items, next_cursor=next_cursor)

    async def events(
        self, user_id: uuid.UUID, limit: int, cursor: str | None
    ) -> FeedEventsResponse:
        decoded: tuple[date, time, uuid.UUID] | None = None
        if cursor:
            parts = _decode_cursor(cursor)
            if len(parts) != 3:
                raise HTTPException(status_code=400, detail="Invalid cursor")
            try:
                decoded = (
                    date.fromisoformat(parts[0]),
                    time.fromisoformat(parts[1]),
                    uuid.UUID(parts[2]),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid cursor") from exc

        today = datetime.now(DHAKA_TZ).date()
        rows = await self.repo.events_feed(user_id, today, limit, decoded)
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            FeedEventItem(
                event_id=ev.event_id,
                masjid_id=ev.masjid_id,
                masjid_name=name,
                title=ev.title,
                description=ev.description,
                event_date=ev.event_date,
                event_time=ev.event_time,
                location=ev.location,
                capacity=ev.capacity,
                attendee_count=attendees,
                is_rsvped=rsvped,
            )
            for ev, name, attendees, rsvped in rows
        ]
        next_cursor = None
        if has_more and rows:
            last_ev = rows[-1][0]
            next_cursor = _encode_cursor(
                [
                    last_ev.event_date.isoformat(),
                    last_ev.event_time.isoformat(),
                    str(last_ev.event_id),
                ]
            )
        return FeedEventsResponse(items=items, next_cursor=next_cursor)
