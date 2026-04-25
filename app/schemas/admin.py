import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AdminStatsResponse(BaseModel):
    total_masjids: int
    active_masjids: int
    pending_masjids: int
    suspended_masjids: int
    verified_masjids: int
    total_announcements: int
    published_announcements: int
    total_users: int
    active_campaigns: int


class AuditLogEntry(BaseModel):
    log_id: uuid.UUID
    admin_id: uuid.UUID
    admin_email: str | None
    admin_role: str
    action: str
    target_entity: str | None
    target_id: uuid.UUID | None
    ip_address: str | None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogEntry]
    total: int
    page: int
    page_size: int


class AppUserResponse(BaseModel):
    user_id: uuid.UUID
    display_name: str | None
    madhab: str | None
    profile_photo_url: str | None
    is_suspended: bool
    suspended_at: datetime | None
    suspension_reason: str | None
    is_deleted: bool
    created_at: datetime


class AppUserListResponse(BaseModel):
    items: list[AppUserResponse]
    total: int
    page: int
    page_size: int


class SuspendRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class UserGrowthPoint(BaseModel):
    period: str
    count: int


class UserGrowthResponse(BaseModel):
    data: list[UserGrowthPoint]
    period: str
