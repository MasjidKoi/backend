from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.masjid_service import MasjidService


def get_masjid_service(db: AsyncSession = Depends(get_db)) -> MasjidService:
    """Instantiates MasjidService scoped to the current request's AsyncSession."""
    return MasjidService(db)
