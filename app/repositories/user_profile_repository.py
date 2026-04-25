import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models.user_profile import UserProfile
from app.repositories.base import BaseRepository


class UserProfileRepository(BaseRepository[UserProfile]):
    model = UserProfile

    async def get_by_user_id(self, user_id: uuid.UUID) -> UserProfile | None:
        result = await self.db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, user_id: uuid.UUID, email: str | None) -> UserProfile:
        profile = await self.get_by_user_id(user_id)
        if profile is not None:
            return profile
        profile = UserProfile(user_id=user_id)
        self.db.add(profile)
        await self.db.flush()
        return profile

    async def update(self, profile: UserProfile, fields: dict) -> UserProfile:
        for k, v in fields.items():
            setattr(profile, k, v)
        await self.db.flush()
        return profile

    async def soft_delete(self, profile: UserProfile) -> UserProfile:
        profile.is_deleted = True
        profile.deletion_requested_at = datetime.now(timezone.utc)
        await self.db.flush()
        return profile

    async def list_all(
        self, search: str | None, offset: int, limit: int
    ) -> tuple[list[UserProfile], int]:
        filters = [UserProfile.is_deleted == False]  # noqa: E712
        if search:
            filters.append(UserProfile.display_name.ilike(f"%{search}%"))
        count_q = select(func.count()).where(*filters)
        rows_q = (
            select(UserProfile)
            .where(*filters)
            .order_by(UserProfile.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        total = (await self.db.execute(count_q)).scalar_one()
        rows = list((await self.db.execute(rows_q)).scalars().all())
        return rows, total

    async def count_non_deleted(self) -> int:
        result = await self.db.execute(
            select(func.count()).where(UserProfile.is_deleted == False)  # noqa: E712
        )
        return result.scalar_one()

    async def get_growth(self, period: str) -> list[tuple[str, int]]:
        trunc = {"daily": "day", "weekly": "week", "monthly": "month"}[period]
        date_col = func.date_trunc(trunc, UserProfile.created_at).label("bucket")
        result = await self.db.execute(
            select(date_col, func.count().label("cnt"))
            .where(UserProfile.is_deleted == False)  # noqa: E712
            .group_by("bucket")
            .order_by("bucket")
        )
        return [(str(row[0].date()), row[1]) for row in result.all()]
