import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class CoAdminInviteCreate(BaseModel):
    email: EmailStr


class CoAdminInviteResponse(BaseModel):
    invite_id: uuid.UUID
    masjid_id: uuid.UUID
    invited_email: str
    invited_by_email: str | None
    gotrue_user_id: uuid.UUID | None
    status: str
    expires_at: datetime
    resend_count: int
    created_at: datetime
    updated_at: datetime


class CoAdminInviteListResponse(BaseModel):
    items: list[CoAdminInviteResponse]
    total: int
    page: int
    page_size: int


class CoAdminAcceptRequest(BaseModel):
    token: str
    password: str = Field(..., min_length=8)


class CoAdminDeclineRequest(BaseModel):
    token: str
