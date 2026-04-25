import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TicketCategory = Literal["Bug", "IncorrectData", "FeatureRequest", "Other"]
TicketStatus = Literal["Open", "InProgress", "Resolved"]


class SupportTicketCreate(BaseModel):
    category: TicketCategory
    subject: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)


class SupportTicketUpdate(BaseModel):
    status: TicketStatus | None = None
    assigned_to: uuid.UUID | None = None
    assigned_to_email: str | None = Field(default=None, max_length=255)


class SupportTicketResponse(BaseModel):
    ticket_id: uuid.UUID
    category: str
    status: str
    created_at: datetime


class SupportTicketAdminResponse(BaseModel):
    ticket_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None
    category: str
    subject: str | None
    description: str | None
    status: str
    assigned_to: uuid.UUID | None
    assigned_to_email: str | None
    created_at: datetime
    updated_at: datetime


class SupportTicketListResponse(BaseModel):
    items: list[SupportTicketAdminResponse]
    total: int
    page: int
    page_size: int
