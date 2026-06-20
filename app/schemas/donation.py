import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

from app.models.enums import DonationCategory

# BDT bounds enforced at the boundary AND in the service AND by a DB CHECK.
DonationAmount = Annotated[
    Decimal, Field(ge=10, le=500000, max_digits=12, decimal_places=2)
]


class DonationCreate(BaseModel):
    """Body for a general (non-campaign) donation to a masjid."""

    amount: DonationAmount
    category: DonationCategory = DonationCategory.GENERAL
    is_anonymous: bool = False
    # Collected once on first donation (PRD 01 deferral) so the receipt carries
    # the donor's legal name.
    donor_name: str | None = Field(default=None, max_length=255)


class CampaignDonationCreate(BaseModel):
    """Body for a campaign donation — category is forced to CAMPAIGN server-side
    and the masjid is derived from the campaign."""

    amount: DonationAmount
    is_anonymous: bool = False
    donor_name: str | None = Field(default=None, max_length=255)


class CheckoutInitResponse(BaseModel):
    """Handed to the mobile app to open the SSLCommerz WebView."""

    donation_id: uuid.UUID
    status: str
    gross_amount: Decimal
    # "Masjid receives ~৳X" — an ESTIMATE from the configured fee rate; the
    # ledger stores the actual fee from the validated IPN.
    estimated_net: Decimal
    gateway_url: str


class DonationStatusResponse(BaseModel):
    """The status-poll target; the app shows success only when status=completed."""

    donation_id: uuid.UUID
    status: str
    category: str
    masjid_id: uuid.UUID
    campaign_id: uuid.UUID | None
    gross_amount: Decimal
    fee_amount: Decimal
    net_amount: Decimal
    is_anonymous: bool
    receipt_number: str | None
    gateway_payment_method: str | None
    completed_at: datetime | None
    created_at: datetime


class DonationHistoryItem(BaseModel):
    donation_id: uuid.UUID
    masjid_id: uuid.UUID
    masjid_name: str
    campaign_id: uuid.UUID | None
    category: str
    gross_amount: Decimal
    status: str
    is_anonymous: bool
    receipt_number: str | None
    created_at: datetime
    completed_at: datetime | None


class DonationHistoryResponse(BaseModel):
    items: list[DonationHistoryItem]
    # Opaque keyset cursor (the last item's created_at); null when exhausted.
    next_cursor: datetime | None = None


class PerMasjidTotal(BaseModel):
    masjid_id: uuid.UUID
    masjid_name: str
    total: Decimal


class DonationSummaryResponse(BaseModel):
    """All GROSS — donor-facing numbers are gross everywhere."""

    lifetime_total: Decimal
    this_year_total: Decimal
    year: int
    per_masjid: list[PerMasjidTotal]


# ── Admin (masjid-scoped; anonymity mask applied in the query/service layer) ──


class AdminDonationItem(BaseModel):
    donation_id: uuid.UUID
    # "Anonymous donor" where is_anonymous, the donor's name otherwise — the mask
    # is applied server-side so the client never sees a concealed identity.
    donor_label: str
    category: str
    gross_amount: Decimal
    net_amount: Decimal
    status: str
    created_at: datetime
    completed_at: datetime | None


class AdminDonationListResponse(BaseModel):
    items: list[AdminDonationItem]
    total: int
    page: int
    page_size: int
