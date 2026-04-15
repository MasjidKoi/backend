import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import MasjidStatus


class Masjid(Base):
    __tablename__ = "masjids"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','active','suspended','removed')",
            name="ck_masjids_status",
        ),
    )

    masjid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    admin_region: Mapped[str] = mapped_column(String(100), nullable=False)
    location: Mapped[object] = mapped_column(
        # spatial_index=False — Alembic migration manages the GIST index explicitly.
        # Default spatial_index=True would auto-create the index at CREATE TABLE time,
        # causing a DuplicateTable error when Alembic then tries to create it again.
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MasjidStatus.PENDING
    )
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    donations_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    timezone: Mapped[str] = mapped_column(
        String(50), default="Asia/Dhaka", nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    suspension_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # lazy="raise" is mandatory for async SQLAlchemy — prevents accidental
    # lazy-load which raises MissingGreenlet instead of silently hanging.
    facilities: Mapped["MasjidFacilities"] = relationship(
        "MasjidFacilities",
        back_populates="masjid",
        uselist=False,
        lazy="raise",
        cascade="all, delete-orphan",
    )
    contact: Mapped["MasjidContact"] = relationship(
        "MasjidContact",
        back_populates="masjid",
        uselist=False,
        lazy="raise",
        cascade="all, delete-orphan",
    )
    photos: Mapped[list["MasjidPhoto"]] = relationship(
        "MasjidPhoto",
        back_populates="masjid",
        order_by="MasjidPhoto.display_order",
        lazy="raise",
        cascade="all, delete-orphan",
    )
    prayer_times: Mapped[list["PrayerTimeRecord"]] = relationship(  # type: ignore[name-defined]
        "PrayerTimeRecord",
        back_populates="masjid",
        lazy="raise",
        cascade="all, delete-orphan",
    )
    jumah_schedule: Mapped["JumahSchedule"] = relationship(  # type: ignore[name-defined]
        "JumahSchedule",
        back_populates="masjid",
        uselist=False,
        lazy="raise",
        cascade="all, delete-orphan",
    )


class MasjidFacilities(Base):
    __tablename__ = "masjid_facilities"

    # masjid_id is both PK and FK — expresses 1:1 without a surrogate key
    masjid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="CASCADE"),
        primary_key=True,
    )
    has_sisters_section: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    has_wudu_area: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_wudu_male: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_wudu_female: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    has_wheelchair_access: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    has_parking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    parking_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_janazah: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_school: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    imam_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    imam_qualifications: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    masjid: Mapped["Masjid"] = relationship(
        "Masjid", back_populates="facilities", lazy="raise"
    )


class MasjidContact(Base):
    __tablename__ = "masjid_contact"

    masjid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="CASCADE"),
        primary_key=True,
    )
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    whatsapp: Mapped[str | None] = mapped_column(String(20), nullable=True)
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    masjid: Mapped["Masjid"] = relationship(
        "Masjid", back_populates="contact", lazy="raise"
    )


class MasjidPhoto(Base):
    __tablename__ = "masjid_photos"

    photo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    masjid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    is_cover: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    masjid: Mapped["Masjid"] = relationship(
        "Masjid", back_populates="photos", lazy="raise"
    )
