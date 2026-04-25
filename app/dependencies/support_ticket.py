from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.support_ticket_service import SupportTicketService


def get_support_ticket_service(
    db: AsyncSession = Depends(get_db),
) -> SupportTicketService:
    return SupportTicketService(db)
