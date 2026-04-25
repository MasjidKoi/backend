import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MasjidReviewCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    body: str | None = Field(default=None, max_length=1000)


class MasjidReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    review_id: uuid.UUID
    masjid_id: uuid.UUID
    user_id: uuid.UUID
    rating: int
    body: str | None
    reviewer_display_name: str | None
    created_at: datetime


class MasjidReviewListResponse(BaseModel):
    items: list[MasjidReviewResponse]
    total: int
    page: int
    page_size: int
    average_rating: float | None
