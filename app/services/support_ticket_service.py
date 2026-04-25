import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.support_ticket_repository import SupportTicketRepository
from app.schemas.support_ticket import (
    SupportTicketAdminResponse,
    SupportTicketCreate,
    SupportTicketListResponse,
    SupportTicketResponse,
    SupportTicketUpdate,
)
from app.services.email_service import send_email


class SupportTicketService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = SupportTicketRepository(db)
        self.audit = AuditLogRepository(db)

    async def submit_ticket(
        self, user: CurrentUser, data: SupportTicketCreate
    ) -> SupportTicketResponse:
        ticket = await self.repo.create(
            user_id=user.user_id,
            user_email=user.email,
            category=data.category,
            subject=data.subject,
            description=data.description,
            status="Open",
        )
        await self.repo.commit()
        return SupportTicketResponse(
            ticket_id=ticket.ticket_id,
            category=ticket.category,
            status=ticket.status,
            created_at=ticket.created_at,
        )

    async def list_tickets(
        self,
        status_filter: str | None,
        category_filter: str | None,
        page: int,
        page_size: int,
    ) -> SupportTicketListResponse:
        rows, total = await self.repo.list(
            status_filter,
            category_filter,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return SupportTicketListResponse(
            items=[_to_admin_response(t) for t in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def update_ticket(
        self,
        ticket_id: uuid.UUID,
        data: SupportTicketUpdate,
        user: CurrentUser,
    ) -> SupportTicketAdminResponse:
        ticket = await self.repo.get_by_id(ticket_id)
        if not ticket:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found"
            )

        fields = data.model_dump(exclude_unset=True)
        await self.repo.update(ticket, fields)

        if data.status == "Resolved" and ticket.user_email:
            await send_email(
                to=ticket.user_email,
                subject="Your MasjidKoi support ticket has been resolved",
                body=(
                    "Thank you for contacting us.\n\n"
                    "Your support ticket has been reviewed and resolved.\n\n"
                    "JazakAllah Khair,\nThe MasjidKoi Team"
                ),
            )

        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="update_support_ticket",
            target_entity="support_ticket",
            target_id=ticket_id,
            details=fields,
        )
        await self.repo.commit()
        ticket = await self.repo.get_by_id(ticket_id)
        return _to_admin_response(ticket)


def _to_admin_response(ticket) -> SupportTicketAdminResponse:
    return SupportTicketAdminResponse(
        ticket_id=ticket.ticket_id,
        user_id=ticket.user_id,
        user_email=ticket.user_email,
        category=ticket.category,
        subject=ticket.subject,
        description=ticket.description,
        status=ticket.status,
        assigned_to=ticket.assigned_to,
        assigned_to_email=ticket.assigned_to_email,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )
