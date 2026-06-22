import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Low-star reviews must justify themselves: a 1–2 star rating requires a short
# body so the warning helps others; 3–5 may be stars-only. Enforced in the
# service (cross-field) rather than here so the error is a clean 422 detail.
LOW_STAR_MIN_BODY = 20


class MasjidReviewUpsert(BaseModel):
    """Body for PUT /masjids/{id}/reviews — create or fully replace my review."""

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
    edited: bool
    created_at: datetime
    updated_at: datetime


class MasjidReviewListResponse(BaseModel):
    items: list[MasjidReviewResponse]
    total: int
    page: int
    page_size: int
    average_rating: float | None
    # Count of reviews per star value (keys "1"–"5"); drives the distribution bars.
    rating_distribution: dict[int, int] | None = None
