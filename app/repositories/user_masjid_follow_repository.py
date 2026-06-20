import uuid

from sqlalchemy import delete, func, select

from app.models.masjid import Masjid
from app.models.user_masjid_follow import UserMasjidFollow
from app.repositories.base import BaseRepository


class UserMasjidFollowRepository(BaseRepository[UserMasjidFollow]):
    model = UserMasjidFollow

    async def follow(
        self, user_id: uuid.UUID, masjid_id: uuid.UUID
    ) -> UserMasjidFollow:
        existing = await self._get(user_id, masjid_id)
        if existing:
            return existing
        row = UserMasjidFollow(user_id=user_id, masjid_id=masjid_id)
        self.db.add(row)
        await self.db.flush()
        return row

    async def unfollow(self, user_id: uuid.UUID, masjid_id: uuid.UUID) -> None:
        await self.db.execute(
            delete(UserMasjidFollow).where(
                UserMasjidFollow.user_id == user_id,
                UserMasjidFollow.masjid_id == masjid_id,
            )
        )
        await self.db.flush()

    async def is_following(self, user_id: uuid.UUID, masjid_id: uuid.UUID) -> bool:
        return (await self._get(user_id, masjid_id)) is not None

    async def list_by_user(self, user_id: uuid.UUID) -> list[UserMasjidFollow]:
        result = await self.db.execute(
            select(UserMasjidFollow).where(UserMasjidFollow.user_id == user_id)
        )
        return list(result.scalars().all())

    async def count_by_masjid(self, masjid_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).where(UserMasjidFollow.masjid_id == masjid_id)
        )
        return result.scalar_one()

    async def list_masjids_for_user(self, user_id: uuid.UUID) -> list:
        result = await self.db.execute(
            select(Masjid, UserMasjidFollow.followed_at)
            .join(UserMasjidFollow, UserMasjidFollow.masjid_id == Masjid.masjid_id)
            .where(UserMasjidFollow.user_id == user_id)
            .order_by(UserMasjidFollow.followed_at.desc())
        )
        return result.all()

    async def set_mode(
        self, user_id: uuid.UUID, masjid_id: uuid.UUID, mode: str
    ) -> UserMasjidFollow | None:
        follow = await self._get(user_id, masjid_id)
        if follow is None:
            return None
        follow.notification_mode = mode
        await self.db.flush()
        return follow

    async def list_follows_with_masjid(self, user_id: uuid.UUID) -> list:
        """(Masjid, notification_mode) for every masjid the user follows — drives
        the notification-preferences screen. Newest follow first."""
        result = await self.db.execute(
            select(Masjid, UserMasjidFollow.notification_mode)
            .join(UserMasjidFollow, UserMasjidFollow.masjid_id == Masjid.masjid_id)
            .where(UserMasjidFollow.user_id == user_id)
            .order_by(UserMasjidFollow.followed_at.desc())
        )
        return result.all()

    async def list_user_ids_following_masjid_in_mode(
        self, masjid_id: uuid.UUID, mode: str
    ) -> list[uuid.UUID]:
        """User ids following a masjid in a given notification mode — used by the
        instant announcement notifier (mode='instant')."""
        result = await self.db.execute(
            select(UserMasjidFollow.user_id).where(
                UserMasjidFollow.masjid_id == masjid_id,
                UserMasjidFollow.notification_mode == mode,
            )
        )
        return list(result.scalars().all())

    async def list_user_ids_following_masjid_not_muted(
        self, masjid_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """User ids following a masjid in any non-muted mode (instant + digest)
        — the audience for prayer-time-change pushes (PRD 03 TIME_CHANGE). Unlike
        announcements, time changes are time-sensitive and aren't folded into the
        daily digest, so digest-mode followers are notified directly here."""
        result = await self.db.execute(
            select(UserMasjidFollow.user_id).where(
                UserMasjidFollow.masjid_id == masjid_id,
                UserMasjidFollow.notification_mode != "mute",
            )
        )
        return list(result.scalars().all())

    async def list_digest_masjid_ids_for_user(
        self, user_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Masjid ids the user follows in digest mode — used by the digest job."""
        result = await self.db.execute(
            select(UserMasjidFollow.masjid_id).where(
                UserMasjidFollow.user_id == user_id,
                UserMasjidFollow.notification_mode == "digest",
            )
        )
        return list(result.scalars().all())

    async def _get(
        self, user_id: uuid.UUID, masjid_id: uuid.UUID
    ) -> UserMasjidFollow | None:
        result = await self.db.execute(
            select(UserMasjidFollow).where(
                UserMasjidFollow.user_id == user_id,
                UserMasjidFollow.masjid_id == masjid_id,
            )
        )
        return result.scalar_one_or_none()
