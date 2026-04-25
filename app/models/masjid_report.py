import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class MasjidReport(Base):
    __tablename__ = "masjid_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','reviewed','resolved')",
            name="ck_masjid_reports_status",
        ),
    )

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # SET NULL so reports survive masjid deletion (audit trail preservation)
    masjid_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reporter_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    masjid: Mapped["Masjid | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Masjid",
        back_populates="reports",
        lazy="raise",
    )
