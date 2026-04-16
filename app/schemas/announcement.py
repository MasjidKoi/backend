import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AnnouncementCreate(BaseModel):
    title: str = Field(..., max_length=200)
    body: str = Field(..., min_length=1)
    publish: bool = False   # True = publish immediately; False = save as draft


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    body: str | None = None


class AnnouncementResponse(BaseModel):
    announcement_id: uuid.UUID
    masjid_id: uuid.UUID
    title: str
    body: str
    is_published: bool
    published_at: datetime | None
    posted_by_email: str | None
    created_at: datetime
    updated_at: datetime


class AnnouncementListResponse(BaseModel):
    items: list[AnnouncementResponse]
    total: int
    page: int
    page_size: int


class AnnouncementWithMasjidResponse(AnnouncementResponse):
    masjid_name: str


class AnnouncementPlatformListResponse(BaseModel):
    items: list[AnnouncementWithMasjidResponse]
    total: int
    page: int
    page_size: int
