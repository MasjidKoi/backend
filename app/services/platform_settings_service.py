from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.models.platform_settings import PlatformSettings
from app.repositories.platform_settings_repository import PlatformSettingsRepository
from app.schemas.platform_settings import PlatformSettingsUpdate


class PlatformSettingsService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = PlatformSettingsRepository(db)

    async def get(self) -> PlatformSettings:
        return await self.repo.get_or_create()

    async def update(
        self, data: PlatformSettingsUpdate, user: CurrentUser
    ) -> PlatformSettings:
        settings = await self.repo.get_or_create()
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(settings, k, v)
        settings.updated_by_email = user.email
        await self.repo.db.flush()
        await self.repo.commit()
        return settings
