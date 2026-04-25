import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class CheckInCreate(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)


class BadgeResponse(BaseModel):
    badge_id: uuid.UUID
    badge_type: str
    earned_at: datetime


class CheckInResponse(BaseModel):
    checkin_id: uuid.UUID
    masjid_id: uuid.UUID | None
    checked_in_at: datetime
    new_badges: list[BadgeResponse]


class CheckInHistoryItem(BaseModel):
    checkin_id: uuid.UUID
    masjid_id: uuid.UUID | None
    masjid_name: str | None
    checked_in_at: datetime


class CheckInHistoryResponse(BaseModel):
    items: list[CheckInHistoryItem]
    total: int
    page: int
    page_size: int


class StreakResponse(BaseModel):
    current_streak: int
    total_checkins: int


class JournalEntryCreate(BaseModel):
    entry_date: date
    prayers_logged: str | None = Field(default=None, max_length=200)
    quran_pages: int | None = Field(default=None, ge=0, le=600)
    notes: str | None = Field(default=None, max_length=3000)


class JournalEntryResponse(BaseModel):
    journal_id: uuid.UUID
    entry_date: date
    prayers_logged: str | None
    quran_pages: int | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class JournalListResponse(BaseModel):
    items: list[JournalEntryResponse]
    total: int
    page: int
    page_size: int
