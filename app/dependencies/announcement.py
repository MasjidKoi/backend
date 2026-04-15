from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.announcement_service import AnnouncementService


def get_announcement_service(db: AsyncSession = Depends(get_db)) -> AnnouncementService:
    return AnnouncementService(db)
