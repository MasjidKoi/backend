import uuid
from datetime import date, datetime, time

from pydantic import BaseModel


class FeedAnnouncementItem(BaseModel):
    kind: str = "announcement"
    announcement_id: uuid.UUID
    masjid_id: uuid.UUID
    masjid_name: str
    title: str
    body: str
    published_at: datetime
    created_at: datetime


class FeedEventItem(BaseModel):
    kind: str = "event"
    event_id: uuid.UUID
    masjid_id: uuid.UUID
    masjid_name: str
    title: str
    description: str
    event_date: date
    event_time: time
    event_end_time: time | None
    location: str
    capacity: int | None
    attendee_count: int
    is_rsvped: bool


class FeedAnnouncementsResponse(BaseModel):
    items: list[FeedAnnouncementItem]
    next_cursor: str | None


class FeedEventsResponse(BaseModel):
    items: list[FeedEventItem]
    next_cursor: str | None
