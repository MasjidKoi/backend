import uuid

from fastapi import APIRouter, Depends, Query, status

from app.core.security import CurrentUser
from app.dependencies.auth import get_current_user, require_platform_admin
from app.dependencies.support_ticket import get_support_ticket_service
from app.schemas.support_ticket import (
    SupportTicketAdminResponse,
    SupportTicketCreate,
    SupportTicketListResponse,
    SupportTicketResponse,
    SupportTicketUpdate,
    TicketCategory,
    TicketStatus,
)
from app.services.support_ticket_service import SupportTicketService

user_router = APIRouter(prefix="/support", tags=["support"])
admin_router = APIRouter(prefix="/admin/support", tags=["support"])


@user_router.post(
    "/tickets",
    response_model=SupportTicketResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit an in-app bug report or feedback (authenticated user)",
)
async def submit_ticket(
    body: SupportTicketCreate,
    user: CurrentUser = Depends(get_current_user),
    service: SupportTicketService = Depends(get_support_ticket_service),
) -> SupportTicketResponse:
    return await service.submit_ticket(user, body)


@admin_router.get(
    "/tickets",
    response_model=SupportTicketListResponse,
    summary="List all support tickets (platform_admin)",
)
async def list_tickets(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: TicketStatus | None = Query(default=None),
    category: TicketCategory | None = Query(default=None),
    _user: CurrentUser = Depends(require_platform_admin),
    service: SupportTicketService = Depends(get_support_ticket_service),
) -> SupportTicketListResponse:
    return await service.list_tickets(status, category, page, page_size)


@admin_router.patch(
    "/tickets/{ticket_id}",
    response_model=SupportTicketAdminResponse,
    summary="Assign or resolve a support ticket (platform_admin)",
)
async def update_ticket(
    ticket_id: uuid.UUID,
    body: SupportTicketUpdate,
    user: CurrentUser = Depends(require_platform_admin),
    service: SupportTicketService = Depends(get_support_ticket_service),
) -> SupportTicketAdminResponse:
    return await service.update_ticket(ticket_id, body, user)
