"""
Prayer times router.

Shares the /masjids prefix with the masjids router.
Route ordering: /{id}/prayer-times/recalc MUST come before /{id}/prayer-times
or FastAPI will try to parse "recalc" as a date query param.
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, status

from app.core.security import CurrentUser
from app.dependencies.auth import require_masjid_admin
from app.dependencies.prayer_times import get_prayer_time_service
from app.schemas.prayer_times import (
    JumahResponse,
    JumahUpdate,
    PrayerTimeManualUpdate,
    PrayerTimeRecalcRequest,
    PrayerTimeResponse,
    PrayerTimesListResponse,
)
from app.services.prayer_time_service import PrayerTimeService

router = APIRouter(prefix="/masjids", tags=["prayer-times"])


@router.get(
    "/{masjid_id}/prayer-times",
    response_model=PrayerTimesListResponse,
    summary="Get prayer times — auto-calculates and caches on first request (public)",
    description=(
        "Returns prayer times for the given date (defaults to today in the masjid's "
        "local timezone). Use `days=1-7` to fetch multiple days in one call "
        "(mobile 7-day cache). Times are in local wall-clock format 'HH:MM'. "
        "Iqamah fields are null until the masjid admin sets them."
    ),
)
async def get_prayer_times(
    masjid_id: uuid.UUID,
    prayer_date: date | None = Query(
        default=None,
        alias="date",
        description="Local date (YYYY-MM-DD). Defaults to today.",
    ),
    days: int = Query(
        default=1,
        ge=1,
        le=7,
        description="Number of consecutive days to return (max 7).",
    ),
    service: PrayerTimeService = Depends(get_prayer_time_service),
) -> PrayerTimesListResponse:
    return await service.get_prayer_times(
        masjid_id=masjid_id,
        prayer_date=prayer_date,
        days=days,
    )


@router.post(
    "/{masjid_id}/prayer-times/recalc",
    response_model=PrayerTimeResponse,
    summary="Force recalculate prayer times (masjid_admin)",
    description=(
        "Recalculates using the adhan library. Clears is_manual flag. "
        "Existing iqamah times are preserved. "
        "Optionally override calculation_method or madhab."
    ),
)
async def recalculate_prayer_times(
    masjid_id: uuid.UUID,
    body: PrayerTimeRecalcRequest,
    user: CurrentUser = Depends(require_masjid_admin),
    service: PrayerTimeService = Depends(get_prayer_time_service),
) -> PrayerTimeResponse:
    return await service.recalculate(masjid_id, body, user)


@router.put(
    "/{masjid_id}/prayer-times",
    response_model=PrayerTimeResponse,
    summary="Manually set prayer times (masjid_admin)",
    description=(
        "Set specific azan and/or iqamah times for a date. "
        "Only provided fields are written — unset fields keep existing values. "
        "Works for both initial setup and subsequent updates. "
        "If azan times are not provided and no record exists, they are "
        "auto-calculated as a base."
    ),
)
async def set_prayer_times(
    masjid_id: uuid.UUID,
    body: PrayerTimeManualUpdate,
    user: CurrentUser = Depends(require_masjid_admin),
    service: PrayerTimeService = Depends(get_prayer_time_service),
) -> PrayerTimeResponse:
    return await service.manual_override(masjid_id, body, user)


@router.get(
    "/{masjid_id}/jumah",
    response_model=JumahResponse,
    summary="Get Friday prayer schedule (public)",
)
async def get_jumah(
    masjid_id: uuid.UUID,
    service: PrayerTimeService = Depends(get_prayer_time_service),
) -> JumahResponse:
    return await service.get_jumah(masjid_id)


@router.put(
    "/{masjid_id}/jumah",
    response_model=JumahResponse,
    summary="Update Friday prayer schedule (masjid_admin)",
    description="All fields are optional. Only provided fields are written.",
)
async def update_jumah(
    masjid_id: uuid.UUID,
    body: JumahUpdate,
    user: CurrentUser = Depends(require_masjid_admin),
    service: PrayerTimeService = Depends(get_prayer_time_service),
) -> JumahResponse:
    return await service.update_jumah(masjid_id, body, user)
