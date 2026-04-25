from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.user_masjid_follow_service import UserMasjidFollowService


def get_follow_service(db: AsyncSession = Depends(get_db)) -> UserMasjidFollowService:
    return UserMasjidFollowService(db)
