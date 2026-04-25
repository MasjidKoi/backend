import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.repositories.masjid_repository import MasjidRepository
from app.repositories.user_masjid_follow_repository import UserMasjidFollowRepository


class UserMasjidFollowService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = UserMasjidFollowRepository(db)
        self.masjid_repo = MasjidRepository(db)

    async def follow(self, masjid_id: uuid.UUID, user: CurrentUser) -> None:
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Masjid not found"
            )
        await self.repo.follow(uuid.UUID(str(user.user_id)), masjid_id)
        await self.repo.commit()

    async def unfollow(self, masjid_id: uuid.UUID, user: CurrentUser) -> None:
        await self.repo.unfollow(uuid.UUID(str(user.user_id)), masjid_id)
        await self.repo.commit()

    async def get_follower_count(self, masjid_id: uuid.UUID) -> dict:
        count = await self.repo.count_by_masjid(masjid_id)
        return {"count": count}
