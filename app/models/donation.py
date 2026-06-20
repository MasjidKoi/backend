import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Donation(Base):
    """A single donation — the ledger's atom (PRD 05).

    donation_id doubles as the SSLCommerz ``tran_id``, so an IPN echoing the
    tran_id resolves to this row by primary key. ``gateway_val_id`` is the
    idempotency key: a replayed or duplicate IPN that re-validates the same
    val_id is a no-op. Financial records outlive masjid removal, so the masjid
    and campaign FKs use ``ondelete=RESTRICT`` (not CASCADE) — a masjid is
    soft-removed via its status, never hard-deleted out from under its ledger.

    Money is gross / fee / net at the moment of completion: the donor pays
    ``gross_amount``; the gateway keeps ``fee_amount``; ``net_amount`` is what
    is credited to the masjid's derived balance. The DB enforces
    ``net = gross - fee`` so reconciliation against SSLCommerz settlement
    reports is arithmetic, not archaeology.
    """

    __tablename__ = "donations"
    __table_args__ = (
        CheckConstraint(
            "category IN ('general','building','zakat','sadaqah','lillah','campaign')",
            name="ck_donations_category",
        ),
        CheckConstraint(
            "status IN ('pending','completed','refunded','failed')",
            name="ck_donations_status",
        ),
        CheckConstraint(
            "gross_amount >= 10 AND gross_amount <= 500000",
            name="ck_donations_gross_bounds",
        ),
        CheckConstraint(
            "net_amount = gross_amount - fee_amount",
            name="ck_donations_net_arithmetic",
        ),
        # Campaign donations iff a campaign is attached — mismatched pairs are
        # unrepresentable, so a client can never aim a campaign donation wrong.
        CheckConstraint(
            "(category = 'campaign') = (campaign_id IS NOT NULL)",
            name="ck_donations_campaign_category",
        ),
        Index("ix_donations_user_created", "user_id", "created_at"),
        Index("ix_donations_masjid_status", "masjid_id", "status"),
        Index(
            "ix_donations_campaign",
            "campaign_id",
            postgresql_where=text("campaign_id IS NOT NULL"),
        ),
        Index("ix_donations_status_created", "status", "created_at"),
    )

    donation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    masjid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="RESTRICT"),
        nullable=False,
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjid_campaigns.campaign_id", ondelete="RESTRICT"),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    net_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_anonymous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Donor identity snapshot — captured at checkout so receipts carry the
    # donor's legal name and the confirmation email has an address, without a
    # GoTrue round-trip per IPN. Anonymity hides these from the masjid admin and
    # public surfaces, never from the donor or the platform audit trail.
    donor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    donor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gateway_session_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    gateway_val_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    # Captured at completion; needed to initiate a gateway refund later.
    gateway_bank_tran_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gateway_payment_method: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    receipt_number: Mapped[str | None] = mapped_column(
        String(30), nullable=True, unique=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refunded_at: Mapped[datetime | None] = mapped_column(
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
