import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserBadge(Base):
    __tablename__ = "user_badges"
    __table_args__ = (
        CheckConstraint(
            "badge_type IN ('FajrWarrior','GenerousGiver','CommunityPillar')",
            name="ck_user_badges_type",
        ),
        UniqueConstraint("user_id", "badge_type", name="uq_user_badge_type"),
        Index("idx_user_badges_user", "user_id"),
    )

    badge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    badge_type: Mapped[str] = mapped_column(String(30), nullable=False)
    earned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
