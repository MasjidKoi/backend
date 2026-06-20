from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.notification_pref_service import NotificationPreferenceService


def get_notification_pref_service(
    db: AsyncSession = Depends(get_db),
) -> NotificationPreferenceService:
    return NotificationPreferenceService(db)
