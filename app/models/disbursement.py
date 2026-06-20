import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Disbursement(Base):
    """A manually-recorded payout from the NGO to a masjid (PRD 05).

    Disbursement (MVP) is ledger + manual recording: the NGO pays outside the
    system (bank / bKash / cash) and records amount, date, method, reference.
    A masjid's balance is **derived, never stored** — SUM(net of completed
    donations) − SUM(disbursements) — so there is one source of truth and no
    drift. Balances may go negative after a post-disbursement refund and offset
    against future giving, which is why this row never updates a stored balance.
    """

    __tablename__ = "disbursements"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_disbursements_amount_positive"),
        CheckConstraint(
            "method IN ('bank','bkash','cash')",
            name="ck_disbursements_method",
        ),
    )

    disbursement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    masjid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    disbursed_on: Mapped[date] = mapped_column(Date, nullable=False)
    recorded_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
