import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, Field


class EventCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = Field(..., min_length=1)
    event_date: date
    event_time: time
    location: str = Field(..., max_length=300)
    capacity: int | None = Field(default=None, ge=1)
    rsvp_enabled: bool = False


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    event_date: date | None = None
    event_time: time | None = None
    location: str | None = Field(default=None, max_length=300)
    capacity: int | None = None
    rsvp_enabled: bool | None = None


class EventResponse(BaseModel):
    event_id: uuid.UUID
    masjid_id: uuid.UUID
    title: str
    description: str
    event_date: date
    event_time: time
    location: str
    capacity: int | None
    rsvp_enabled: bool
    rsvp_count: int
    created_by_email: str | None
    created_at: datetime
    updated_at: datetime


class EventListResponse(BaseModel):
    items: list[EventResponse]
    total: int
    page: int
    page_size: int


class EventAttendeeResponse(BaseModel):
    user_id: uuid.UUID
    rsvp_at: datetime


class EventAttendeeListResponse(BaseModel):
    items: list[EventAttendeeResponse]
    total: int
    page: int
    page_size: int
