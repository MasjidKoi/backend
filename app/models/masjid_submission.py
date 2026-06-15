import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import MasjidSubmissionStatus


class MasjidSubmission(Base):
    """
    A community-submitted "missing masjid", held in a table SEPARATE from the live
    `masjids` table by construction — unreviewed data must never reach public
    queries (nearby/search read `masjids`, never this table). A platform admin
    reviews each submission and, on approval, creates a real masjid through the
    normal create path; `approved_masjid_id` links the two so the submitter can
    "view it live".

    Coordinates are stored as plain floats (not PostGIS) — there are no spatial
    queries over submissions; dedupe happens client-side against the public
    nearby endpoint (~150 m).
    """

    __tablename__ = "masjid_submissions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_masjid_submissions_status",
        ),
        # Supports the per-user pending-cap count and the GET /me/submissions read.
        Index("ix_masjid_submissions_user_status", "user_id", "status"),
    )

    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # No FK — users live in GoTrue's auth schema, not a local table.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=MasjidSubmissionStatus.PENDING
    )
    # SET NULL so a submission survives deletion of the masjid it spawned.
    approved_masjid_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    approved_masjid: Mapped["Masjid | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Masjid",
        lazy="raise",
    )
