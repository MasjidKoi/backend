import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.config import settings

# ── Create ───────────────────────────────────────────────────────────────────


class MasjidSubmissionCreate(BaseModel):
    """Consumer submission. Mandatory: name + coordinates."""

    name: str = Field(..., min_length=2, max_length=200)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    address: str | None = Field(default=None, max_length=500)
    photo_key: str | None = Field(default=None, max_length=500)


# ── Photo upload ───────────────────────────────────────────────────────────────


class SubmissionPhotoUploadResponse(BaseModel):
    """Returned by the pre-submission photo upload — the client puts `photo_key`
    on the subsequent submission create body; `url` is for an immediate preview."""

    photo_key: str
    url: str


# ── Approve ──────────────────────────────────────────────────────────────────


class MasjidSubmissionApprove(BaseModel):
    """
    Platform-admin confirmation before the masjid goes live. `admin_region` is
    required because it is mandatory on a real masjid but not collected from the
    submitter; name/address may be corrected at approval time, otherwise the
    submitted values are used.
    """

    admin_region: str = Field(..., max_length=100)
    name: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=500)


# ── Responses ────────────────────────────────────────────────────────────────


class MasjidSubmissionResponse(BaseModel):
    """The submitter's view (GET /me/submissions, POST response)."""

    model_config = ConfigDict(from_attributes=True)
    submission_id: uuid.UUID
    name: str
    latitude: float
    longitude: float
    address: str | None
    photo_key: str | None
    status: str
    approved_masjid_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def photo_url(self) -> str | None:
        """Viewable URL for the attached photo (so the submitter and the NGO
        review queue can see it), derived from the stored key."""
        if not self.photo_key:
            return None
        return f"{settings.s3_public_url}/{settings.S3_BUCKET_PHOTOS}/{self.photo_key}"


class MasjidSubmissionAdminResponse(MasjidSubmissionResponse):
    """Admin view — adds the submitter id."""

    user_id: uuid.UUID


class MasjidSubmissionListResponse(BaseModel):
    items: list[MasjidSubmissionAdminResponse]
    total: int
    page: int
    page_size: int
