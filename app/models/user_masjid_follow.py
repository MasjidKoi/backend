import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserMasjidFollow(Base):
    __tablename__ = "user_masjid_follows"
    __table_args__ = (
        UniqueConstraint("user_id", "masjid_id", name="uq_user_masjid_follow"),
        CheckConstraint(
            "notification_mode IN ('digest','instant','mute')",
            name="ck_user_masjid_follow_mode",
        ),
    )

    # Composite PK — (user_id, masjid_id) is already unique
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, nullable=False
    )
    masjid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        index=True,
    )
    followed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # PRD 07 — per-masjid announcement notification mode. Defaults to 'digest';
    # existing rows backfill to 'digest' via the column server_default.
    notification_mode: Mapped[str] = mapped_column(
        String(20), server_default="digest", nullable=False
    )

    masjid: Mapped["Masjid"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Masjid", back_populates="followers", lazy="raise"
    )
