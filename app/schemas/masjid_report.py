import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class MasjidReportCreate(BaseModel):
    field_name: str = Field(..., max_length=100)
    description: str = Field(..., min_length=10)
    reporter_email: EmailStr | None = None


class MasjidReportResponse(BaseModel):
    report_id: uuid.UUID
    status: str
    created_at: datetime


class MasjidReportUpdateStatus(BaseModel):
    status: Literal["reviewed", "resolved"]


class MasjidReportAdminResponse(BaseModel):
    report_id: uuid.UUID
    masjid_id: uuid.UUID | None
    field_name: str
    description: str
    reporter_email: str | None
    status: str
    created_at: datetime


class MasjidReportListResponse(BaseModel):
    items: list[MasjidReportAdminResponse]
    total: int
    page: int
    page_size: int
