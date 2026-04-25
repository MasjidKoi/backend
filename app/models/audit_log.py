"""
Audit log — append-only record of every admin write action.

NO updated_at column. Records are immutable once created.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # GoTrue user_id (sub claim from JWT)
    admin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    admin_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    admin_role: Mapped[str] = mapped_column(String(30), nullable=False)

    # What happened
    action: Mapped[str] = mapped_column(
        String(80), nullable=False
    )  # e.g. "create_masjid"
    target_entity: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # e.g. "masjid"
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Append-only — no updated_at
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
