from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.admin_service import AdminService


def get_admin_service(db: AsyncSession = Depends(get_db)) -> AdminService:
    return AdminService(db)
