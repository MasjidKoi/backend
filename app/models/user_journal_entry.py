import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserJournalEntry(Base):
    """One day's ibadah journal for a user (PRD 08).

    The five prayers are structured booleans (a streak day requires all five);
    Qur'an progress is an amount + unit. ``is_protected`` is the deliberately
    ambiguous protected-day marker — server-side a freeze pass-through and an
    exemption pass-through are one indistinguishable representation, so "zero
    logs + unbroken streak" can never reveal an exemption (PRD §52). The reason
    (frozen vs exempt) lives only in device-local secure storage.
    """

    __tablename__ = "user_journal_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "entry_date", name="uq_journal_user_date"),
        CheckConstraint(
            "quran_unit IS NULL OR quran_unit IN ('pages', 'juz', 'minutes')",
            name="ck_journal_quran_unit",
        ),
        Index("idx_journal_user_date", "user_id", "entry_date"),
    )

    journal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)

    fajr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dhuhr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    asr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    maghrib: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    isha: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    quran_amount: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    quran_unit: Mapped[str | None] = mapped_column(String(10), nullable=True)

    is_protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
