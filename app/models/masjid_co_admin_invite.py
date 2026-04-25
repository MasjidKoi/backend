import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class MasjidCoAdminInvite(Base):
    __tablename__ = "masjid_co_admin_invites"
    __table_args__ = (
        CheckConstraint(
            "status IN ('Pending','Accepted','Declined','Revoked','Expired')",
            name="ck_co_admin_invites_status",
        ),
        Index("idx_co_admin_invites_masjid", "masjid_id"),
        Index("idx_co_admin_invites_gotrue_user", "gotrue_user_id"),
    )

    invite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    masjid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="CASCADE"),
        nullable=False,
    )
    invited_email: Mapped[str] = mapped_column(String(255), nullable=False)
    invited_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    invited_by_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gotrue_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="Pending"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resend_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_resent_at: Mapped[datetime | None] = mapped_column(
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

    masjid: Mapped["Masjid"] = relationship(  # type: ignore[name-defined]
        "Masjid", back_populates="co_admin_invites", lazy="raise"
    )
