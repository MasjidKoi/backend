import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        Index("ix_user_profiles_is_deleted", "is_deleted"),
        CheckConstraint(
            "digest_hour BETWEEN 0 AND 23", name="ck_user_profiles_digest_hour"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    madhab: Mapped[str | None] = mapped_column(String(20), nullable=True)
    profile_photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suspension_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # PRD 07 — daily-digest delivery hour (0–23), interpreted in Asia/Dhaka.
    # Default 19:00. last_digest_sent_at makes the digest job idempotent across
    # restarts and enforces one-push-per-user-per-day.
    digest_hour: Mapped[int] = mapped_column(
        Integer, server_default="19", nullable=False
    )
    last_digest_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # PRD 05 #3 — seeds DonationCreate.is_anonymous when the client omits it, so a
    # user who wants every gift private doesn't re-toggle on each donation.
    donate_anonymously_by_default: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    # PRD 05 #4 / PRD 09 #28 — per-message-type push opt-outs. Each gates one
    # group of non-essential pushes in PushService's fan-out; transactional /
    # correctness pushes (donation confirmed, payment recovery, Hijri offset) have
    # no switch by design, and masjid announcements/digest/time-change stay gated
    # per-follow via notification_mode.
    mute_donation_nudge: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    mute_campaign_milestone: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    mute_moderation_outcome: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    mute_promotions: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    # PRD 09 #1 — set by the purge job once a soft-deleted account is anonymized
    # past the 30-day window; its presence makes the sweep idempotent.
    purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
