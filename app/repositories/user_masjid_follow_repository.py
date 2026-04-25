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
