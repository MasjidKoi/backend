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
            donate_anonymously_by_default=profile.donate_anonymously_by_default,
            mute_donation_nudge=profile.mute_donation_nudge,
            mute_campaign_milestone=profile.mute_campaign_milestone,
            mute_moderation_outcome=profile.mute_moderation_outcome,
            mute_promotions=profile.mute_promotions,
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

    async def update_preferences(
        self, user: CurrentUser, fields: dict
    ) -> NotificationPreferencesResponse:
        """Persist whichever global-preference fields were supplied. ``fields`` is
        the caller's ``model_dump(exclude_unset=True)`` — its keys map 1:1 to
        UserProfile columns, so an empty dict is a harmless no-op read."""
        # Every preference column is NOT NULL, so an explicit JSON null means
        # "leave unchanged", never "set to NULL" — drop it rather than let a
        # NULL UPDATE 500 on the constraint.
        fields = {k: v for k, v in fields.items() if v is not None}
        user_uuid = uuid.UUID(str(user.user_id))
        profile = await self.profile_repo.get_or_create(user_uuid, user.email)
        if fields:
            await self.profile_repo.update(profile, fields)
        await self.profile_repo.commit()
        return await self._build_response(user)
