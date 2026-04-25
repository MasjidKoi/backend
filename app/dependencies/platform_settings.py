from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.platform_settings_service import PlatformSettingsService


def get_platform_settings_service(
    db: AsyncSession = Depends(get_db),
) -> PlatformSettingsService:
    return PlatformSettingsService(db)
