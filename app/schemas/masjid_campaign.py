import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CampaignStatus = Literal["Active", "Completed", "Cancelled"]


class CampaignCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: str | None = None
    target_amount: Decimal = Field(..., gt=0, decimal_places=2)
    banner_url: str | None = Field(default=None, max_length=500)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def end_after_start(self) -> "CampaignCreate":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class CampaignUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    target_amount: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    banner_url: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: CampaignStatus | None = None


class CampaignResponse(BaseModel):
    campaign_id: uuid.UUID
    masjid_id: uuid.UUID
    title: str
    description: str | None
    target_amount: Decimal
    raised_amount: Decimal
    progress_pct: float
    banner_url: str | None
    start_date: date
    end_date: date
    days_remaining: int
    status: str
    created_by_email: str | None
    created_at: datetime
    updated_at: datetime


class CampaignListResponse(BaseModel):
    items: list[CampaignResponse]
    total: int
    page: int
    page_size: int


class CampaignAnalyticsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    campaign_id: uuid.UUID
    title: str
    status: str
    target_amount: Decimal
    raised_amount: Decimal
    progress_pct: float
    days_remaining: int
    donor_count: int
    average_donation: Decimal | None
