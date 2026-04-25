import uuid

from sqlalchemy import func, select

from app.models.user_badge import UserBadge
from app.repositories.base import BaseRepository


class UserBadgeRepository(BaseRepository[UserBadge]):
    model = UserBadge

    async def list_by_user(self, user_id: uuid.UUID) -> list[UserBadge]:
        result = await self.db.execute(
            select(UserBadge)
            .where(UserBadge.user_id == user_id)
            .order_by(UserBadge.earned_at.asc())
        )
        return list(result.scalars().all())

    async def has_badge(self, user_id: uuid.UUID, badge_type: str) -> bool:
        result = await self.db.execute(
            select(func.count()).where(
                UserBadge.user_id == user_id,
                UserBadge.badge_type == badge_type,
            )
        )
        return result.scalar_one() > 0

    async def award(self, user_id: uuid.UUID, badge_type: str) -> UserBadge:
        badge = UserBadge(user_id=user_id, badge_type=badge_type)
        self.db.add(badge)
        await self.db.flush()
        return badge
