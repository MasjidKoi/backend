"""
Prayer time repository.

Three upsert strategies for different write paths:
  upsert_calculated  — GET cache-miss: DO NOTHING (race-condition safe)
  upsert_manual      — PUT override: DO UPDATE with COALESCE (preserves unset fields)
  upsert_recalculated — POST recalc: DO UPDATE (replaces azan, preserves iqamah)
"""

import uuid
from datetime import date
from datetime import time as dt_time

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.prayer_times import JumahSchedule, PrayerTimeRecord
from app.repositories.base import BaseRepository


class PrayerTimeRepository(BaseRepository[PrayerTimeRecord]):
    model = PrayerTimeRecord

    # ── Reads ──────────────────────────────────────────────────────────────────

    async def get_by_masjid_and_date(
        self, masjid_id: uuid.UUID, prayer_date: date
    ) -> PrayerTimeRecord | None:
        result = await self.db.execute(
            select(PrayerTimeRecord).where(
                PrayerTimeRecord.masjid_id == masjid_id,
                PrayerTimeRecord.date == prayer_date,
            )
        )
        return result.scalar_one_or_none()

    async def get_range(
        self,
        masjid_id: uuid.UUID,
        start_date: date,
        end_date: date,
    ) -> list[PrayerTimeRecord]:
        """Inclusive date range — for multi-day GET (mobile 7-day cache)."""
        result = await self.db.execute(
            select(PrayerTimeRecord)
            .where(
                PrayerTimeRecord.masjid_id == masjid_id,
                PrayerTimeRecord.date >= start_date,
                PrayerTimeRecord.date <= end_date,
            )
            .order_by(PrayerTimeRecord.date)
        )
        return list(result.scalars().all())

    # ── Writes ─────────────────────────────────────────────────────────────────

    async def upsert_calculated(
        self,
        *,
        masjid_id: uuid.UUID,
        prayer_date: date,
        fajr_azan: dt_time,
        dhuhr_azan: dt_time,
        asr_azan: dt_time,
        maghrib_azan: dt_time,
        isha_azan: dt_time,
        calculation_method: str,
        madhab: str,
    ) -> PrayerTimeRecord:
        """
        INSERT ... ON CONFLICT DO NOTHING, then SELECT.

        Race-condition safe (edge case 2): two simultaneous GETs for the same
        masjid/date both issue the INSERT. One wins, the other is silently
        discarded. Both then SELECT the authoritative row.

        DO NOTHING (not DO UPDATE) is deliberate: if a manual record already
        exists for this date, we must NOT overwrite it with calculated values.
        """
        stmt = (
            pg_insert(PrayerTimeRecord)
            .values(
                masjid_id=masjid_id,
                date=prayer_date,
                fajr_azan=fajr_azan,
                dhuhr_azan=dhuhr_azan,
                asr_azan=asr_azan,
                maghrib_azan=maghrib_azan,
                isha_azan=isha_azan,
                is_manual=False,
                calculation_method=calculation_method,
                madhab=madhab,
            )
            .on_conflict_do_nothing(index_elements=["masjid_id", "date"])
        )
        await self.db.execute(stmt)
        await self.db.flush()

        # Always SELECT — the row may have been inserted by a concurrent request
        result = await self.db.execute(
            select(PrayerTimeRecord).where(
                PrayerTimeRecord.masjid_id == masjid_id,
                PrayerTimeRecord.date == prayer_date,
            )
        )
        return result.scalar_one()

    async def upsert_manual(
        self,
        *,
        masjid_id: uuid.UUID,
        prayer_date: date,
        fajr_azan: dt_time | None = None,
        dhuhr_azan: dt_time | None = None,
        asr_azan: dt_time | None = None,
        maghrib_azan: dt_time | None = None,
        isha_azan: dt_time | None = None,
        fajr_iqamah: dt_time | None = None,
        dhuhr_iqamah: dt_time | None = None,
        asr_iqamah: dt_time | None = None,
        maghrib_iqamah: dt_time | None = None,
        isha_iqamah: dt_time | None = None,
        calculation_method: str | None = None,
        madhab: str | None = None,
    ) -> PrayerTimeRecord:
        """
        INSERT ... ON CONFLICT DO UPDATE with COALESCE.

        Edge case 5 (partial override): COALESCE keeps existing values for
        any field not included in this call. So an admin can set only
        fajr_iqamah without touching any other field.

        Edge case 7 (repeated PUT): DO UPDATE makes subsequent PUTs idempotent.

        is_manual is set to True on this path unconditionally.
        """
        insert_values: dict = {
            "masjid_id": masjid_id,
            "date": prayer_date,
            "is_manual": True,
        }
        if calculation_method:
            insert_values["calculation_method"] = calculation_method
        if madhab:
            insert_values["madhab"] = madhab

        # Only include non-None time values so column defaults apply on INSERT
        time_fields = {
            "fajr_azan": fajr_azan,
            "dhuhr_azan": dhuhr_azan,
            "asr_azan": asr_azan,
            "maghrib_azan": maghrib_azan,
            "isha_azan": isha_azan,
            "fajr_iqamah": fajr_iqamah,
            "dhuhr_iqamah": dhuhr_iqamah,
            "asr_iqamah": asr_iqamah,
            "maghrib_iqamah": maghrib_iqamah,
            "isha_iqamah": isha_iqamah,
        }
        insert_values.update({k: v for k, v in time_fields.items() if v is not None})

        # COALESCE: use new value if provided, otherwise keep existing DB value
        update_set = {
            "is_manual": True,
            "updated_at": text("now()"),
        }
        for col in (
            "fajr_azan",
            "dhuhr_azan",
            "asr_azan",
            "maghrib_azan",
            "isha_azan",
            "fajr_iqamah",
            "dhuhr_iqamah",
            "asr_iqamah",
            "maghrib_iqamah",
            "isha_iqamah",
        ):
            update_set[col] = text(f"COALESCE(EXCLUDED.{col}, prayer_times.{col})")

        stmt = (
            pg_insert(PrayerTimeRecord)
            .values(**insert_values)
            .on_conflict_do_update(
                index_elements=["masjid_id", "date"],
                set_=update_set,
            )
        )
        await self.db.execute(stmt)
        await self.db.flush()
        # expire_all() clears the session identity-map cache so the SELECT
        # below fetches the actual post-upsert values from the DB, not the
        # cached pre-upsert ORM object.
        self.db.expire_all()
        result = await self.db.execute(
            select(PrayerTimeRecord).where(
                PrayerTimeRecord.masjid_id == masjid_id,
                PrayerTimeRecord.date == prayer_date,
            )
        )
        return result.scalar_one()

    async def upsert_recalculated(
        self,
        *,
        masjid_id: uuid.UUID,
        prayer_date: date,
        fajr_azan: dt_time,
        dhuhr_azan: dt_time,
        asr_azan: dt_time,
        maghrib_azan: dt_time,
        isha_azan: dt_time,
        calculation_method: str,
        madhab: str,
    ) -> PrayerTimeRecord:
        """
        Force overwrite azan times with fresh calculation; set is_manual=False.
        Preserves existing iqamah times — recalculating astronomy should not
        wipe times that the admin manually set.
        """
        stmt = (
            pg_insert(PrayerTimeRecord)
            .values(
                masjid_id=masjid_id,
                date=prayer_date,
                fajr_azan=fajr_azan,
                dhuhr_azan=dhuhr_azan,
                asr_azan=asr_azan,
                maghrib_azan=maghrib_azan,
                isha_azan=isha_azan,
                is_manual=False,
                calculation_method=calculation_method,
                madhab=madhab,
            )
            .on_conflict_do_update(
                index_elements=["masjid_id", "date"],
                set_={
                    "fajr_azan": fajr_azan,
                    "dhuhr_azan": dhuhr_azan,
                    "asr_azan": asr_azan,
                    "maghrib_azan": maghrib_azan,
                    "isha_azan": isha_azan,
                    "is_manual": False,
                    "calculation_method": calculation_method,
                    "madhab": madhab,
                    # Preserve manually-set iqamah times
                    "fajr_iqamah": text("prayer_times.fajr_iqamah"),
                    "dhuhr_iqamah": text("prayer_times.dhuhr_iqamah"),
                    "asr_iqamah": text("prayer_times.asr_iqamah"),
                    "maghrib_iqamah": text("prayer_times.maghrib_iqamah"),
                    "isha_iqamah": text("prayer_times.isha_iqamah"),
                    "updated_at": text("now()"),
                },
            )
        )
        await self.db.execute(stmt)
        await self.db.flush()
        self.db.expire_all()
        result = await self.db.execute(
            select(PrayerTimeRecord).where(
                PrayerTimeRecord.masjid_id == masjid_id,
                PrayerTimeRecord.date == prayer_date,
            )
        )
        return result.scalar_one()


class JumahRepository(BaseRepository[JumahSchedule]):
    model = JumahSchedule

    async def get_by_masjid(self, masjid_id: uuid.UUID) -> JumahSchedule | None:
        result = await self.db.execute(
            select(JumahSchedule).where(JumahSchedule.masjid_id == masjid_id)
        )
        return result.scalar_one_or_none()

    async def upsert(self, masjid_id: uuid.UUID, fields: dict) -> JumahSchedule:
        """
        INSERT ... ON CONFLICT (masjid_id) DO UPDATE.
        Handles first-time PUT and subsequent updates identically.
        """
        stmt = (
            pg_insert(JumahSchedule)
            .values(masjid_id=masjid_id, **fields)
            .on_conflict_do_update(
                index_elements=["masjid_id"],
                set_={**fields, "updated_at": text("now()")},
            )
            .returning(JumahSchedule)
        )
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.scalar_one()
