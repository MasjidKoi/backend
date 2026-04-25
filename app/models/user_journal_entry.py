import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, SmallInteger, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserJournalEntry(Base):
    __tablename__ = "user_journal_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "entry_date", name="uq_journal_user_date"),
        Index("idx_journal_user_date", "user_id", "entry_date"),
    )

    journal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    prayers_logged: Mapped[str | None] = mapped_column(Text, nullable=True)
    quran_pages: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
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
