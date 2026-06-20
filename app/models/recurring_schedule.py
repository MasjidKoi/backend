import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RecurringSchedule(Base):
    """A recurring donation reminder — schedule + nudge, never an auto-charge (PRD 05).

    Hosted checkout yields no token, so a weekly/monthly/nightly commitment is a
    schedule that fires a push each cycle; tapping it opens a prefilled checkout
    needing only the gateway confirm. The donation a nudge produces is an
    ordinary donation with **no FK back to this schedule** — the schedule is a
    pure reminder engine. Missed cycles lapse silently (no stacking of missed
    amounts). The "last 10 nights" preset is a date-bounded NIGHTLY schedule.
    """

    __tablename__ = "recurring_schedules"
    __table_args__ = (
        CheckConstraint(
            "category IN ('general','building','zakat','sadaqah','lillah','campaign')",
            name="ck_recurring_category",
        ),
        CheckConstraint(
            "frequency IN ('weekly','monthly','nightly')",
            name="ck_recurring_frequency",
        ),
        CheckConstraint(
            "status IN ('active','paused','cancelled')",
            name="ck_recurring_status",
        ),
        CheckConstraint(
            "amount >= 10 AND amount <= 500000",
            name="ck_recurring_amount_bounds",
        ),
        CheckConstraint(
            "(category = 'campaign') = (campaign_id IS NOT NULL)",
            name="ck_recurring_campaign_category",
        ),
        # The nudge sweep selects active schedules whose next_due_at has passed.
        Index("ix_recurring_due", "next_due_at", "status"),
        Index("ix_recurring_user", "user_id"),
    )

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    masjid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjid_campaigns.campaign_id", ondelete="CASCADE"),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    frequency: Mapped[str] = mapped_column(String(10), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active"
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
