import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


# ── Nested schemas ─────────────────────────────────────────────────────────────


class FacilitiesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    has_sisters_section: bool
    has_wudu_area: bool
    has_wudu_male: bool
    has_wudu_female: bool
    has_wheelchair_access: bool
    has_parking: bool
    parking_capacity: int | None
    has_janazah: bool
    has_school: bool
    imam_name: str | None
    imam_qualifications: str | None
    imam_languages: str | None
    capacity_male: int | None
    capacity_female: int | None
    updated_at: datetime


class FacilitiesUpdate(BaseModel):
    has_sisters_section: bool | None = None
    has_wudu_area: bool | None = None
    has_wudu_male: bool | None = None
    has_wudu_female: bool | None = None
    has_wheelchair_access: bool | None = None
    has_parking: bool | None = None
    parking_capacity: int | None = None
    has_janazah: bool | None = None
    has_school: bool | None = None
    imam_name: str | None = None
    imam_qualifications: str | None = None
    imam_languages: str | None = None
    capacity_male: int | None = Field(default=None, ge=0, le=100_000)
    capacity_female: int | None = Field(default=None, ge=0, le=100_000)


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    phone: str | None
    email: str | None
    whatsapp: str | None
    website_url: str | None
    updated_at: datetime


class ContactUpdate(BaseModel):
    phone: str | None = None
    email: EmailStr | None = None
    whatsapp: str | None = None
    website_url: str | None = None


class PhotoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    photo_id: uuid.UUID
    url: str
    is_cover: bool
    display_order: int
    created_at: datetime


# ── Create ─────────────────────────────────────────────────────────────────────


class MasjidCreate(BaseModel):
    name: str = Field(..., max_length=200)
    address: str
    admin_region: str = Field(..., max_length=100)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    timezone: str = Field(default="Asia/Dhaka", max_length=50)
    description: str | None = None


# ── Update ─────────────────────────────────────────────────────────────────────


class MasjidUpdate(BaseModel):
    """All fields optional — true PATCH semantics."""

    name: str | None = Field(default=None, max_length=200)
    address: str | None = None
    admin_region: str | None = Field(default=None, max_length=100)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    timezone: str | None = Field(default=None, max_length=50)
    description: str | None = None
    donations_enabled: bool | None = None
    # Service enforces platform_admin-only for status changes
    status: str | None = None
    facilities: FacilitiesUpdate | None = None
    contact: ContactUpdate | None = None


# ── List / summary ─────────────────────────────────────────────────────────────


class MasjidSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    masjid_id: uuid.UUID
    name: str
    address: str
    admin_region: str
    status: str
    verified: bool
    donations_enabled: bool
    created_at: datetime
    updated_at: datetime


class MasjidNearbyResult(MasjidSummary):
    distance_m: float


class MasjidAdminListResponse(BaseModel):
    items: list[MasjidSummary]
    total: int
    page: int
    page_size: int


# ── Full response ──────────────────────────────────────────────────────────────


class MasjidResponse(BaseModel):
    """
    Full masjid profile with coordinates, facilities, contact, and photos.
    Built explicitly in service via _orm_to_response() — never from_attributes
    directly because location is a WKBElement that needs to_shape() conversion.
    """

    masjid_id: uuid.UUID
    name: str
    address: str
    admin_region: str
    latitude: float
    longitude: float
    status: str
    verified: bool
    donations_enabled: bool
    timezone: str
    description: str | None
    suspension_reason: str | None
    created_at: datetime
    updated_at: datetime
    facilities: FacilitiesResponse | None = None
    contact: ContactResponse | None = None
    photos: list[PhotoResponse] = []


# ── Photo actions ──────────────────────────────────────────────────────────────


class PhotoReorderRequest(BaseModel):
    ordered_photo_ids: list[uuid.UUID]


# ── Actions ────────────────────────────────────────────────────────────────────


class SuspendRequest(BaseModel):
    reason: str = Field(..., min_length=10, max_length=500)


# ── Merge ──────────────────────────────────────────────────────────────────────


class MasjidMergeRequest(BaseModel):
    source_masjid_id: uuid.UUID
    target_masjid_id: uuid.UUID
    copy_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_different_ids(self) -> "MasjidMergeRequest":
        if self.source_masjid_id == self.target_masjid_id:
            raise ValueError("source_masjid_id and target_masjid_id must be different")
        return self


# ── Bulk import ────────────────────────────────────────────────────────────────


class BulkImportRowError(BaseModel):
    row: int
    reason: str


class BulkImportResponse(BaseModel):
    created: int
    failed: int
    errors: list[BulkImportRowError]
    import_file_key: str
