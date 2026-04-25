from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.co_admin_invite_service import CoAdminInviteService


def get_co_admin_invite_service(
    db: AsyncSession = Depends(get_db),
) -> CoAdminInviteService:
    return CoAdminInviteService(db)
