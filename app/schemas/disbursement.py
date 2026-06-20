import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

from app.models.enums import DisbursementMethod


class DisbursementCreate(BaseModel):
    """Record a manual payout the NGO made to a masjid out of band."""

    amount: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    method: DisbursementMethod
    disbursed_on: date
    reference: str | None = Field(default=None, max_length=255)
    notes: str | None = None


class DisbursementResponse(BaseModel):
    disbursement_id: uuid.UUID
    masjid_id: uuid.UUID
    amount: Decimal
    method: str
    reference: str | None
    disbursed_on: date
    notes: str | None
    created_at: datetime


class RefundRequest(BaseModel):
    reason: str = Field(default="Refund", max_length=500)


class BalanceResponse(BaseModel):
    """A masjid's derived balance: net completed donations − recorded payouts."""

    masjid_id: uuid.UUID
    net_donations: Decimal
    total_disbursed: Decimal
    balance: Decimal


class MasjidBalanceItem(BaseModel):
    masjid_id: uuid.UUID
    masjid_name: str
    net_donations: Decimal
    total_disbursed: Decimal
    balance: Decimal


class BalanceListResponse(BaseModel):
    items: list[MasjidBalanceItem]
