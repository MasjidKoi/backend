from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.prayer_time_service import PrayerTimeService


def get_prayer_time_service(db: AsyncSession = Depends(get_db)) -> PrayerTimeService:
    """Instantiates PrayerTimeService scoped to the current request's AsyncSession."""
    return PrayerTimeService(db)
