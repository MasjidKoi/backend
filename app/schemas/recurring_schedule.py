import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    DonationCategory,
    RecurringFrequency,
    RecurringScheduleStatus,
)

ScheduleAmount = Annotated[
    Decimal, Field(ge=10, le=500000, max_digits=12, decimal_places=2)
]


class RecurringScheduleCreate(BaseModel):
    """Create a recurring reminder. Provide masjid_id OR campaign_id (a campaign
    derives its masjid). The "last 10 nights" preset is frequency=nightly with a
    10-day start/end window."""

    masjid_id: uuid.UUID | None = None
    campaign_id: uuid.UUID | None = None
    amount: ScheduleAmount
    category: DonationCategory = DonationCategory.GENERAL
    frequency: RecurringFrequency
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def _target_required(self) -> "RecurringScheduleCreate":
        if self.masjid_id is None and self.campaign_id is None:
            raise ValueError("Either masjid_id or campaign_id is required")
        return self


class RecurringScheduleUpdate(BaseModel):
    """Pause / resume (status) or change the amount."""

    status: RecurringScheduleStatus | None = None
    amount: ScheduleAmount | None = None


class RecurringScheduleResponse(BaseModel):
    schedule_id: uuid.UUID
    masjid_id: uuid.UUID
    campaign_id: uuid.UUID | None
    category: str
    amount: Decimal
    frequency: str
    start_date: date
    end_date: date | None
    next_due_at: datetime
    status: str
    created_at: datetime


class RecurringScheduleListResponse(BaseModel):
    items: list[RecurringScheduleResponse]
