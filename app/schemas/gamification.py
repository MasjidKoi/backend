import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import QuranUnit


class CheckInCreate(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)


class BadgeResponse(BaseModel):
    """A single earned milestone tier."""

    badge_id: uuid.UUID
    badge_type: str
    tier: int
    earned_at: datetime


class EarnedTier(BaseModel):
    tier: int
    earned_at: datetime


class BadgeFamilyProgress(BaseModel):
    """A family's earned tiers plus progress toward the next — the gallery row.

    ``current_value`` is the live counter (consecutive Fajr days, consecutive
    giving months, or contribution points); ``next_threshold`` is null once the
    top tier is earned.
    """

    badge_type: str
    current_value: int
    current_tier: int  # highest tier earned, 0 if none
    next_threshold: int | None
    earned: list[EarnedTier]


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
    total_checkins: int  # lifetime count — moved here from the streak contract
    page: int
    page_size: int


class StreakResponse(BaseModel):
    """Journal-derived prayer streak (PRD 08). No check-in total — that lives on
    the check-in history contract now."""

    current: int
    longest: int
    freezes_held: int
    freezes_applied: int


class PrayerSet(BaseModel):
    fajr: bool = False
    dhuhr: bool = False
    asr: bool = False
    maghrib: bool = False
    isha: bool = False


class QuranProgress(BaseModel):
    # Upper bound kept well within PostgreSQL SMALLINT (max 32767, the column
    # type) and far above any real daily total (604 pages / 30 juz / minutes).
    amount: int = Field(..., ge=0, le=10000)
    unit: QuranUnit


class JournalEntryUpsert(BaseModel):
    """Field-level upsert: only the groups present in the request are written.

    Omitting ``prayers`` leaves the five booleans untouched (a Qur'an-only edit
    can't clear prayer logs); sending ``quran: null`` clears Qur'an progress.
    """

    entry_date: date
    prayers: PrayerSet | None = None
    quran: QuranProgress | None = None
    notes: str | None = Field(default=None, max_length=3000)
    is_protected: bool | None = None


class JournalEntryResponse(BaseModel):
    journal_id: uuid.UUID
    entry_date: date
    prayers: PrayerSet
    quran: QuranProgress | None
    is_protected: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


class JournalListResponse(BaseModel):
    items: list[JournalEntryResponse]
    total: int
    page: int
    page_size: int
