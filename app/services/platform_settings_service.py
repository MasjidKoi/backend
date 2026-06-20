from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.models.enums import PushMessageType
from app.models.platform_settings import PlatformSettings
from app.repositories.platform_settings_repository import PlatformSettingsRepository
from app.schemas.platform_settings import PlatformSettingsUpdate
from app.services.push_service import PushMessage, PushService


class PlatformSettingsService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = PlatformSettingsRepository(db)

    async def get(self) -> PlatformSettings:
        return await self.repo.get_or_create()

    async def update(
        self, data: PlatformSettingsUpdate, user: CurrentUser
    ) -> PlatformSettings:
        settings = await self.repo.get_or_create()
        old_hijri_offset = settings.hijri_offset_days
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(settings, k, v)
        settings.updated_by_email = user.email
        await self.repo.db.flush()
        await self.repo.commit()
        # onupdate=func.now() sets updated_at server-side (and a freshly-created
        # singleton row never loaded it) — reload before the route serialises it.
        await self.repo.refresh(settings)

        # A changed Hijri offset shifts the calendar for everyone — broadcast a
        # platform-wide ping so clients refresh. Best-effort, after commit: a
        # push failure must never undo the settings write. Only fire on an
        # actual value change, not on every PATCH that happens to touch settings.
        if (
            data.hijri_offset_days is not None
            and settings.hijri_offset_days != old_hijri_offset
        ):
            await PushService(self.repo.db).notify_all(
                PushMessage(
                    message_type=PushMessageType.HIJRI_OFFSET,
                    title="Islamic calendar updated",
                    body="The Hijri date has been adjusted.",
                    data={"hijri_offset_days": settings.hijri_offset_days},
                )
            )
        return settings
