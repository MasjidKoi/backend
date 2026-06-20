import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.repositories.user_masjid_follow_repository import UserMasjidFollowRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.schemas.notification import (
    FollowedMasjidPreference,
    NotificationPreferencesResponse,
)


class NotificationPreferenceService:
    def __init__(self, db: AsyncSession) -> None:
        self.profile_repo = UserProfileRepository(db)
        self.follow_repo = UserMasjidFollowRepository(db)

    async def _build_response(
        self, user: CurrentUser
    ) -> NotificationPreferencesResponse:
        user_uuid = uuid.UUID(str(user.user_id))
        profile = await self.profile_repo.get_or_create(user_uuid, user.email)
        rows = await self.follow_repo.list_follows_with_masjid(user_uuid)
        return NotificationPreferencesResponse(
            digest_hour=profile.digest_hour,
            masjids=[
                FollowedMasjidPreference(
                    masjid_id=masjid.masjid_id,
                    name=masjid.name,
                    notification_mode=mode,
                )
                for masjid, mode in rows
            ],
        )

    async def get_preferences(
        self, user: CurrentUser
    ) -> NotificationPreferencesResponse:
        resp = await self._build_response(user)
        await self.profile_repo.commit()
        return resp

    async def set_digest_hour(
        self, user: CurrentUser, digest_hour: int
    ) -> NotificationPreferencesResponse:
        user_uuid = uuid.UUID(str(user.user_id))
        profile = await self.profile_repo.get_or_create(user_uuid, user.email)
        await self.profile_repo.update(profile, {"digest_hour": digest_hour})
        await self.profile_repo.commit()
        return await self._build_response(user)
