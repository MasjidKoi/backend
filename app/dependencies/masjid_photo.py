from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.storage import get_storage_service
from app.services.masjid_photo_service import MasjidPhotoService
from app.services.storage import StorageService


def get_masjid_photo_service(
    db: AsyncSession = Depends(get_db),
) -> MasjidPhotoService:
    return MasjidPhotoService(db)
