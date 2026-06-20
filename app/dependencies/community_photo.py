from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.community_photo_service import CommunityPhotoService


def get_community_photo_service(
    db: AsyncSession = Depends(get_db),
) -> CommunityPhotoService:
    return CommunityPhotoService(db)
