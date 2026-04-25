"""
Prayer time schemas.

All times are represented as "HH:MM" strings in responses and validated
with Field(pattern=...) in request bodies. This keeps the API human-readable
and avoids timezone ambiguity (times are always local at the masjid).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from datetime import time as dt_time

from pydantic import BaseModel, ConfigDict, Field

_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _fmt(t: dt_time | None) -> str | None:
    """Convert datetime.time → 'HH:MM' string, or None."""
    return t.strftime("%H:%M") if t is not None else None


# ── Prayer time response ───────────────────────────────────────────────────────


class PrayerTimeResponse(BaseModel):
    """
    Single-date prayer time response.
    Times are 'HH:MM' strings representing LOCAL time at the masjid.
    Azan = announcement time. Iqamah = when prayer actually starts (null until admin sets).
    """

    prayer_time_id: uuid.UUID
    masjid_id: uuid.UUID
    date: date

    # Azan — always present
    fajr_azan: str
    dhuhr_azan: str
    asr_azan: str
    maghrib_azan: str
    isha_azan: str

    # Iqamah — null until admin sets them
    fajr_iqamah: str | None
    dhuhr_iqamah: str | None
    asr_iqamah: str | None
    maghrib_iqamah: str | None
    isha_iqamah: str | None

    is_manual: bool
    calculation_method: str
    madhab: str
    updated_at: datetime


class PrayerTimesListResponse(BaseModel):
    """Multi-day response for ?days=N query param (mobile 7-day cache)."""

    dates: list[PrayerTimeResponse]
    total: int


# ── Manual override (PUT) ──────────────────────────────────────────────────────


class PrayerTimeManualUpdate(BaseModel):
    """
    PUT /masjids/{id}/prayer-times

    All fields are optional — only provided fields are written.
    Unset fields keep their existing DB values (via COALESCE in the upsert).

    Masjids often deviate from calculated times due to local custom,
    seasonal adjustments, or organisational preferences.
    Both azan times (announcement) and iqamah times (prayer start)
    can be independently set per prayer.

    Example — only set iqamah times:
        {"date": "2026-04-16", "fajr_iqamah": "04:45", "dhuhr_iqamah": "13:15"}

    Example — set a custom Fajr azan and its iqamah:
        {"date": "2026-04-16", "fajr_azan": "04:30", "fajr_iqamah": "04:45"}
    """

    date: date

    # Azan overrides — admin can set different times from calculated
    fajr_azan: str | None = Field(default=None, pattern=_TIME_PATTERN)
    dhuhr_azan: str | None = Field(default=None, pattern=_TIME_PATTERN)
    asr_azan: str | None = Field(default=None, pattern=_TIME_PATTERN)
    maghrib_azan: str | None = Field(default=None, pattern=_TIME_PATTERN)
    isha_azan: str | None = Field(default=None, pattern=_TIME_PATTERN)

    # Iqamah overrides
    fajr_iqamah: str | None = Field(default=None, pattern=_TIME_PATTERN)
    dhuhr_iqamah: str | None = Field(default=None, pattern=_TIME_PATTERN)
    asr_iqamah: str | None = Field(default=None, pattern=_TIME_PATTERN)
    maghrib_iqamah: str | None = Field(default=None, pattern=_TIME_PATTERN)
    isha_iqamah: str | None = Field(default=None, pattern=_TIME_PATTERN)

    calculation_method: str | None = None
    madhab: str | None = None


# ── Recalculate (POST) ────────────────────────────────────────────────────────


class PrayerTimeRecalcRequest(BaseModel):
    """
    POST /masjids/{id}/prayer-times/recalc

    Force-recalculate using the adhan library.
    Clears is_manual flag. Existing iqamah times are preserved.
    """

    date: date
    calculation_method: str | None = None
    madhab: str | None = None


# ── Jumah schemas ──────────────────────────────────────────────────────────────


class JumahResponse(BaseModel):
    """
    GET /masjids/{id}/jumah

    Returns the standing Friday prayer schedule.
    All time fields are 'HH:MM' strings or null if not yet set.
    """

    masjid_id: uuid.UUID
    khutbah_1_azan: str | None
    khutbah_1_start: str | None
    khutbah_2_azan: str | None
    khutbah_2_start: str | None
    notes: str | None
    updated_at: datetime


class JumahUpdate(BaseModel):
    """
    PUT /masjids/{id}/jumah — all fields optional (true PATCH semantics).
    Only provided fields are written; others keep existing values.
    """

    khutbah_1_azan: str | None = Field(default=None, pattern=_TIME_PATTERN)
    khutbah_1_start: str | None = Field(default=None, pattern=_TIME_PATTERN)
    khutbah_2_azan: str | None = Field(default=None, pattern=_TIME_PATTERN)
    khutbah_2_start: str | None = Field(default=None, pattern=_TIME_PATTERN)
    notes: str | None = Field(default=None, max_length=500)
