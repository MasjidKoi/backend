from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.masjid_event_service import MasjidEventService


def get_event_service(db: AsyncSession = Depends(get_db)) -> MasjidEventService:
    return MasjidEventService(db)
