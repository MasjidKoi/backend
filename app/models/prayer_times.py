import uuid
from datetime import date, datetime
from datetime import time as dt_time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import CalculationMethod, Madhab


class PrayerTimeRecord(Base):
    """
    One row per masjid per calendar date.

    Azan times are always present (calculated by adhan library or overridden).
    Iqamah times are nullable — they are always set manually by the masjid admin.

    is_manual=False → auto-calculated by adhan library (cached on first GET).
    is_manual=True  → admin explicitly set at least one field.

    Times are stored as local wall-clock TIME (no tz offset).
    The UTC offset is derived from Masjid.timezone at read time.
    """

    __tablename__ = "prayer_times"
    __table_args__ = (
        UniqueConstraint("masjid_id", "date", name="uq_prayer_times_masjid_date"),
    )

    prayer_time_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    masjid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)

    # ── Azan times (always present after calculation) ─────────────────────────
    fajr_azan: Mapped[dt_time] = mapped_column(Time, nullable=False)
    dhuhr_azan: Mapped[dt_time] = mapped_column(Time, nullable=False)
    asr_azan: Mapped[dt_time] = mapped_column(Time, nullable=False)
    maghrib_azan: Mapped[dt_time] = mapped_column(Time, nullable=False)
    isha_azan: Mapped[dt_time] = mapped_column(Time, nullable=False)

    # ── Iqamah times (always manually set, initially NULL) ────────────────────
    fajr_iqamah: Mapped[dt_time | None] = mapped_column(Time, nullable=True)
    dhuhr_iqamah: Mapped[dt_time | None] = mapped_column(Time, nullable=True)
    asr_iqamah: Mapped[dt_time | None] = mapped_column(Time, nullable=True)
    maghrib_iqamah: Mapped[dt_time | None] = mapped_column(Time, nullable=True)
    isha_iqamah: Mapped[dt_time | None] = mapped_column(Time, nullable=True)

    # ── Meta ──────────────────────────────────────────────────────────────────
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    calculation_method: Mapped[str] = mapped_column(
        String(40), default=CalculationMethod.KARACHI, nullable=False
    )
    madhab: Mapped[str] = mapped_column(
        String(20), default=Madhab.HANAFI, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # lazy="raise" — mandatory for async; access only via selectinload()
    masjid: Mapped["Masjid"] = relationship(  # type: ignore[name-defined]
        "Masjid",
        back_populates="prayer_times",
        lazy="raise",
    )


class JumahSchedule(Base):
    """
    Per-masjid standing Jumu'ah (Friday prayer) schedule.
    Not date-specific — represents the regular weekly schedule.
    Auto-created as an empty row when a Masjid is created.

    masjid_id is both PK and FK (same 1:1 pattern as MasjidFacilities).
    """

    __tablename__ = "jumah_schedules"

    masjid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="CASCADE"),
        primary_key=True,
    )
    # First Jumu'ah
    khutbah_1_azan: Mapped[dt_time | None] = mapped_column(Time, nullable=True)
    khutbah_1_start: Mapped[dt_time | None] = mapped_column(Time, nullable=True)
    # Second Jumu'ah (some masjids run two sessions for capacity)
    khutbah_2_azan: Mapped[dt_time | None] = mapped_column(Time, nullable=True)
    khutbah_2_start: Mapped[dt_time | None] = mapped_column(Time, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    masjid: Mapped["Masjid"] = relationship(  # type: ignore[name-defined]
        "Masjid",
        back_populates="jumah_schedule",
        lazy="raise",
    )
