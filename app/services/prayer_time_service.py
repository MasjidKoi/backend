"""
Prayer time service — orchestrates calculator, repository, and masjid validation.

This is the only layer that raises HTTPException.
The commit boundary sits here; repositories only flush.
"""

import logging
import uuid
from datetime import date, timedelta
from datetime import time as dt_time

from fastapi import HTTPException, status
from geoalchemy2.shape import to_shape
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.models.enums import CalculationMethod, Madhab
from app.models.masjid import Masjid
from app.models.prayer_times import JumahSchedule, PrayerTimeRecord
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.masjid_repository import MasjidRepository
from app.repositories.prayer_time_repository import (
    JumahRepository,
    PrayerTimeRepository,
)
from app.schemas.prayer_times import (
    JumahResponse,
    JumahUpdate,
    PrayerTimeManualUpdate,
    PrayerTimeRecalcRequest,
    PrayerTimeResponse,
    PrayerTimesListResponse,
    _fmt,
)
from app.services import prayer_calculator as calc

logger = logging.getLogger(__name__)


def _parse_time(s: str | None) -> dt_time | None:
    """Convert 'HH:MM' string → datetime.time, or None."""
    if s is None:
        return None
    h, m = s.split(":")
    return dt_time(int(h), int(m))


class PrayerTimeService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = PrayerTimeRepository(db)
        self.jumah_repo = JumahRepository(db)
        self.masjid_repo = MasjidRepository(db)
        self.audit = AuditLogRepository(db)

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _get_masjid_or_404(self, masjid_id: uuid.UUID) -> Masjid:
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Masjid not found",
            )
        return masjid

    def _check_scope(self, user: CurrentUser, masjid_id: uuid.UUID) -> None:
        if user.is_platform_admin:
            return
        if user.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to your own masjid",
            )

    def _to_response(self, record: PrayerTimeRecord) -> PrayerTimeResponse:
        """Convert ORM record to schema — all times formatted as 'HH:MM'."""
        return PrayerTimeResponse(
            prayer_time_id=record.prayer_time_id,
            masjid_id=record.masjid_id,
            date=record.date,
            fajr_azan=_fmt(record.fajr_azan),  # type: ignore[arg-type]
            dhuhr_azan=_fmt(record.dhuhr_azan),  # type: ignore[arg-type]
            asr_azan=_fmt(record.asr_azan),  # type: ignore[arg-type]
            maghrib_azan=_fmt(record.maghrib_azan),  # type: ignore[arg-type]
            isha_azan=_fmt(record.isha_azan),  # type: ignore[arg-type]
            fajr_iqamah=_fmt(record.fajr_iqamah),
            dhuhr_iqamah=_fmt(record.dhuhr_iqamah),
            asr_iqamah=_fmt(record.asr_iqamah),
            maghrib_iqamah=_fmt(record.maghrib_iqamah),
            isha_iqamah=_fmt(record.isha_iqamah),
            is_manual=record.is_manual,
            calculation_method=record.calculation_method,
            madhab=record.madhab,
            updated_at=record.updated_at,
        )

    def _to_jumah_response(self, j: JumahSchedule) -> JumahResponse:
        return JumahResponse(
            masjid_id=j.masjid_id,
            khutbah_1_azan=_fmt(j.khutbah_1_azan),
            khutbah_1_start=_fmt(j.khutbah_1_start),
            khutbah_2_azan=_fmt(j.khutbah_2_azan),
            khutbah_2_start=_fmt(j.khutbah_2_start),
            notes=j.notes,
            updated_at=j.updated_at,
        )

    def _extract_coords(self, masjid: Masjid) -> tuple[float, float]:
        """Extract lat/lng floats from the GeoAlchemy2 WKBElement."""
        point = to_shape(masjid.location)
        return point.y, point.x  # lat, lng

    async def _auto_calculate_and_cache(
        self,
        masjid: Masjid,
        prayer_date: date,
        method: str,
        madhab: str,
    ) -> PrayerTimeRecord:
        """
        Calculate prayer times via adhan and cache the result.
        Edge case 4: if any time is None, raise 422 — calculation failed.
        Edge case 2: uses DO NOTHING upsert to handle concurrent GETs safely.
        """
        try:
            lat, lng = self._extract_coords(masjid)
            times = calc.calculate(
                lat=lat,
                lng=lng,
                local_date=prayer_date,
                tz_string=masjid.timezone,
                method=method,
                madhab=madhab,
            )
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )

        # Edge case 4: None times (should not happen for Bangladesh lat/lng)
        none_prayers = [
            name
            for name, t in [
                ("fajr", times.fajr),
                ("dhuhr", times.dhuhr),
                ("asr", times.asr),
                ("maghrib", times.maghrib),
                ("isha", times.isha),
            ]
            if t is None
        ]
        if none_prayers:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Prayer times could not be calculated for: {none_prayers}",
            )

        return await self.repo.upsert_calculated(
            masjid_id=masjid.masjid_id,
            prayer_date=prayer_date,
            fajr_azan=times.fajr,  # type: ignore[arg-type]
            dhuhr_azan=times.dhuhr,  # type: ignore[arg-type]
            asr_azan=times.asr,  # type: ignore[arg-type]
            maghrib_azan=times.maghrib,  # type: ignore[arg-type]
            isha_azan=times.isha,  # type: ignore[arg-type]
            calculation_method=method,
            madhab=madhab,
        )

    # ── Public reads ───────────────────────────────────────────────────────────

    async def get_prayer_times(
        self,
        masjid_id: uuid.UUID,
        prayer_date: date | None = None,
        days: int = 1,
    ) -> PrayerTimesListResponse:
        """
        GET /masjids/{id}/prayer-times

        - date=None → use today in the masjid's local timezone (edge case 1)
        - days=1 → single date; days=2..7 → range (for mobile 7-day cache)
        - On cache miss: auto-calculate via adhan + store (edge case 2)
        """
        masjid = await self._get_masjid_or_404(masjid_id)

        if prayer_date is None:
            prayer_date = calc.get_local_date(masjid.timezone)

        days = max(1, min(days, 7))
        end_date = prayer_date + timedelta(days=days - 1)

        # Load all existing records in the range in one query
        existing = await self.repo.get_range(masjid_id, prayer_date, end_date)
        existing_by_date = {r.date: r for r in existing}

        # Default method/madhab from Karachi/Hanafi (appropriate for Bangladesh)
        method = CalculationMethod.KARACHI
        madhab = Madhab.HANAFI

        results: list[PrayerTimeResponse] = []
        current = prayer_date
        while current <= end_date:
            if current in existing_by_date:
                record = existing_by_date[current]
            else:
                # Cache miss → auto-calculate and store
                record = await self._auto_calculate_and_cache(
                    masjid, current, method, madhab
                )
                await self.repo.commit()
            results.append(self._to_response(record))
            current += timedelta(days=1)

        return PrayerTimesListResponse(dates=results, total=len(results))

    async def get_jumah(self, masjid_id: uuid.UUID) -> JumahResponse:
        """GET /masjids/{id}/jumah — returns empty schedule if none set yet."""
        await self._get_masjid_or_404(masjid_id)
        schedule = await self.jumah_repo.get_by_masjid(masjid_id)
        if schedule is None:
            # Return an empty response object without DB row
            from datetime import datetime, timezone

            return JumahResponse(
                masjid_id=masjid_id,
                khutbah_1_azan=None,
                khutbah_1_start=None,
                khutbah_2_azan=None,
                khutbah_2_start=None,
                notes=None,
                updated_at=datetime.now(timezone.utc),
            )
        return self._to_jumah_response(schedule)

    # ── Admin writes ───────────────────────────────────────────────────────────

    async def manual_override(
        self,
        masjid_id: uuid.UUID,
        data: PrayerTimeManualUpdate,
        user: CurrentUser,
    ) -> PrayerTimeResponse:
        """
        PUT /masjids/{id}/prayer-times

        Admin sets specific azan and/or iqamah times for a date.
        COALESCE in the upsert preserves fields not included in this call.
        If no row exists for the date, creates one (auto-calculating missing
        azan fields first if none provided).
        """
        self._check_scope(user, masjid_id)
        masjid = await self._get_masjid_or_404(masjid_id)

        # Always load (or auto-calculate) the base row first.
        # PostgreSQL enforces NOT NULL on INSERT VALUES even when
        # ON CONFLICT DO UPDATE fires — so we must always supply azan fields.
        existing = await self.repo.get_by_masjid_and_date(masjid_id, data.date)
        if existing is None:
            method = data.calculation_method or CalculationMethod.KARACHI
            madhab = data.madhab or Madhab.HANAFI
            existing = await self._auto_calculate_and_cache(
                masjid, data.date, method, madhab
            )
            await self.repo.commit()

        # Merge: use provided value where given, else keep existing DB value
        fajr_azan = _parse_time(data.fajr_azan) or existing.fajr_azan
        dhuhr_azan = _parse_time(data.dhuhr_azan) or existing.dhuhr_azan
        asr_azan = _parse_time(data.asr_azan) or existing.asr_azan
        maghrib_azan = _parse_time(data.maghrib_azan) or existing.maghrib_azan
        isha_azan = _parse_time(data.isha_azan) or existing.isha_azan

        # Iqamah: use provided if field was explicitly sent, else keep existing
        provided = data.model_fields_set
        fajr_iqamah = (
            _parse_time(data.fajr_iqamah)
            if "fajr_iqamah" in provided
            else existing.fajr_iqamah
        )
        dhuhr_iqamah = (
            _parse_time(data.dhuhr_iqamah)
            if "dhuhr_iqamah" in provided
            else existing.dhuhr_iqamah
        )
        asr_iqamah = (
            _parse_time(data.asr_iqamah)
            if "asr_iqamah" in provided
            else existing.asr_iqamah
        )
        maghrib_iqamah = (
            _parse_time(data.maghrib_iqamah)
            if "maghrib_iqamah" in provided
            else existing.maghrib_iqamah
        )
        isha_iqamah = (
            _parse_time(data.isha_iqamah)
            if "isha_iqamah" in provided
            else existing.isha_iqamah
        )

        record = await self.repo.upsert_manual(
            masjid_id=masjid_id,
            prayer_date=data.date,
            fajr_azan=fajr_azan,
            dhuhr_azan=dhuhr_azan,
            asr_azan=asr_azan,
            maghrib_azan=maghrib_azan,
            isha_azan=isha_azan,
            fajr_iqamah=fajr_iqamah,
            dhuhr_iqamah=dhuhr_iqamah,
            asr_iqamah=asr_iqamah,
            maghrib_iqamah=maghrib_iqamah,
            isha_iqamah=isha_iqamah,
            calculation_method=data.calculation_method or existing.calculation_method,
            madhab=data.madhab or existing.madhab,
        )
        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="set_prayer_times",
            target_entity="prayer_times",
            target_id=masjid_id,
        )
        await self.repo.commit()
        return self._to_response(record)

    async def recalculate(
        self,
        masjid_id: uuid.UUID,
        data: PrayerTimeRecalcRequest,
        user: CurrentUser,
    ) -> PrayerTimeResponse:
        """
        POST /masjids/{id}/prayer-times/recalc

        Force recalculation — clears is_manual, preserves iqamah.
        """
        self._check_scope(user, masjid_id)
        masjid = await self._get_masjid_or_404(masjid_id)

        method = data.calculation_method or CalculationMethod.KARACHI
        madhab = data.madhab or Madhab.HANAFI

        try:
            lat, lng = self._extract_coords(masjid)
            times = calc.calculate(
                lat=lat,
                lng=lng,
                local_date=data.date,
                tz_string=masjid.timezone,
                method=method,
                madhab=madhab,
            )
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
            )

        none_prayers = [
            name
            for name, t in [
                ("fajr", times.fajr),
                ("dhuhr", times.dhuhr),
                ("asr", times.asr),
                ("maghrib", times.maghrib),
                ("isha", times.isha),
            ]
            if t is None
        ]
        if none_prayers:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Calculation failed for: {none_prayers}",
            )

        record = await self.repo.upsert_recalculated(
            masjid_id=masjid_id,
            prayer_date=data.date,
            fajr_azan=times.fajr,  # type: ignore[arg-type]
            dhuhr_azan=times.dhuhr,  # type: ignore[arg-type]
            asr_azan=times.asr,  # type: ignore[arg-type]
            maghrib_azan=times.maghrib,  # type: ignore[arg-type]
            isha_azan=times.isha,  # type: ignore[arg-type]
            calculation_method=method,
            madhab=madhab,
        )
        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="recalc_prayer_times",
            target_entity="prayer_times",
            target_id=masjid_id,
        )
        await self.repo.commit()
        return self._to_response(record)

    async def update_jumah(
        self,
        masjid_id: uuid.UUID,
        data: JumahUpdate,
        user: CurrentUser,
    ) -> JumahResponse:
        """PUT /masjids/{id}/jumah — upsert the standing Friday schedule."""
        self._check_scope(user, masjid_id)
        await self._get_masjid_or_404(masjid_id)

        fields = {
            k: _parse_time(v) if k.endswith(("azan", "start")) else v
            for k, v in data.model_dump(exclude_unset=True).items()
            if v is not None
        }
        schedule = await self.jumah_repo.upsert(masjid_id, fields)
        await self.jumah_repo.commit()
        return self._to_jumah_response(schedule)
