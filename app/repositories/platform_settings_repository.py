from sqlalchemy import select

from app.models.platform_settings import PlatformSettings
from app.repositories.base import BaseRepository


class PlatformSettingsRepository(BaseRepository[PlatformSettings]):
    model = PlatformSettings

    async def get_or_create(self) -> PlatformSettings:
        result = await self.db.execute(select(PlatformSettings).limit(1))
        settings = result.scalar_one_or_none()
        if settings is None:
            settings = PlatformSettings()
            self.db.add(settings)
            await self.db.flush()
        return settings
