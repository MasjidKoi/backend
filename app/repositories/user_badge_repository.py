import uuid

from sqlalchemy import select

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

    async def awarded_set(self, user_id: uuid.UUID) -> set[tuple[str, int]]:
        """The (badge_type, tier) pairs already held — the BadgeEngine's
        idempotency input."""
        result = await self.db.execute(
            select(UserBadge.badge_type, UserBadge.tier).where(
                UserBadge.user_id == user_id
            )
        )
        return {(row[0], row[1]) for row in result.all()}

    async def award(self, user_id: uuid.UUID, badge_type: str, tier: int) -> UserBadge:
        badge = UserBadge(user_id=user_id, badge_type=badge_type, tier=tier)
        self.db.add(badge)
        await self.db.flush()
        return badge
