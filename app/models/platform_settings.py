import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PlatformSettings(Base):
    __tablename__ = "platform_settings"

    settings_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # FR-T40-136 — Prayer calculation defaults for new masjids
    default_madhab: Mapped[str] = mapped_column(
        String(20), nullable=False, default="hanafi"
    )
    default_calc_method: Mapped[str] = mapped_column(
        String(30), nullable=False, default="KARACHI"
    )
    # FR-T40-137 — Supported countries (ISO 3166-1 alpha-2 codes)
    supported_countries: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(2)), nullable=True
    )
    # FR-T40-138 — Feature flags
    reviews_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    checkins_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    # FR-T40-139 — Branding
    platform_name: Mapped[str] = mapped_column(
        String(100), nullable=False, default="MasjidKoi"
    )
    # FR-T40-142 — Maintenance mode
    maintenance_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    maintenance_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FR-T40-144 — Terms & Privacy
    terms_of_service: Mapped[str | None] = mapped_column(Text, nullable=True)
    privacy_policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    terms_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Metadata
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
