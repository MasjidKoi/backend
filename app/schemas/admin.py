import uuid
from datetime import datetime

from pydantic import BaseModel


class AdminStatsResponse(BaseModel):
    total_masjids: int
    active_masjids: int
    pending_masjids: int
    suspended_masjids: int
    verified_masjids: int


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
