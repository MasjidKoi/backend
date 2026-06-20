import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

# ── Public ───────────────────────────────────────────────────────────────────


class CommunityPhotoPublic(BaseModel):
    """Public view of an approved community photo (no submitter identity)."""

    model_config = ConfigDict(from_attributes=True)
    photo_id: uuid.UUID
    masjid_id: uuid.UUID
    url: str
    created_at: datetime


class CommunityPhotoPublicListResponse(BaseModel):
    items: list[CommunityPhotoPublic]
    total: int
    page: int
    page_size: int


# ── Submitter (POST response + GET /me/photo-submissions) ──────────────────────


class CommunityPhotoSubmissionResponse(BaseModel):
    """The submitter's view — includes moderation status + timestamps."""

    model_config = ConfigDict(from_attributes=True)
    photo_id: uuid.UUID
    masjid_id: uuid.UUID
    url: str
    status: str
    created_at: datetime
    updated_at: datetime


# ── Moderation (masjid admin + platform admin) ─────────────────────────────────


class CommunityPhotoModerationResponse(CommunityPhotoSubmissionResponse):
    """Moderator view — adds the submitter id."""

    uploaded_by: uuid.UUID | None


class CommunityPhotoModerationListResponse(BaseModel):
    items: list[CommunityPhotoModerationResponse]
    total: int
    page: int
    page_size: int
