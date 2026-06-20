import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DeviceToken(Base):
    """A registered push token for one device of one user (PRD 03 push subsystem).

    user_id carries no FK — users live in GoTrue's auth schema. The token is the
    natural idempotency key (unique); re-registering the same token rotates its
    owner / platform / last_seen rather than duplicating a row. Pruned on logout.
    """

    __tablename__ = "device_tokens"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('ios','android','web')",
            name="ck_device_tokens_platform",
        ),
        Index("ix_device_tokens_user", "user_id"),
    )

    device_token_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    token: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    platform: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
