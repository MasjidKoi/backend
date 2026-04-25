from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.admin_user_service import AdminUserService


def get_admin_user_service(db: AsyncSession = Depends(get_db)) -> AdminUserService:
    return AdminUserService(db)
