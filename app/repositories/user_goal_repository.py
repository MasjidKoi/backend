import uuid
from datetime import date

from sqlalchemy import delete, select

from app.models.user_goal import GoalCompletion, UserGoal
from app.repositories.base import BaseRepository


class UserGoalRepository(BaseRepository[UserGoal]):
    model = UserGoal

    async def get_for_user(
        self, goal_id: uuid.UUID, user_id: uuid.UUID
    ) -> UserGoal | None:
        """Ownership-scoped fetch — a goal is only ever readable by its owner."""
        result = await self.db.execute(
            select(UserGoal).where(
                UserGoal.goal_id == goal_id,
                UserGoal.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self, user_id: uuid.UUID, status: str | None = None
    ) -> list[UserGoal]:
        filters = [UserGoal.user_id == user_id]
        if status is not None:
            filters.append(UserGoal.status == status)
        result = await self.db.execute(
            select(UserGoal).where(*filters).order_by(UserGoal.created_at.desc())
        )
        return list(result.scalars().all())

    # ── Completions (recurring goals) ─────────────────────────────────────────

    async def completion_dates(self, goal_id: uuid.UUID) -> set[date]:
        result = await self.db.execute(
            select(GoalCompletion.completion_date).where(
                GoalCompletion.goal_id == goal_id
            )
        )
        return set(result.scalars().all())

    async def completion_dates_for_goals(
        self, goal_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, set[date]]:
        """Batched completion dates for many goals — one query grouped in memory,
        so listing N recurring goals costs a single round-trip instead of N."""
        if not goal_ids:
            return {}
        result = await self.db.execute(
            select(GoalCompletion.goal_id, GoalCompletion.completion_date).where(
                GoalCompletion.goal_id.in_(goal_ids)
            )
        )
        out: dict[uuid.UUID, set[date]] = {gid: set() for gid in goal_ids}
        for goal_id, completion_date in result.all():
            out[goal_id].add(completion_date)
        return out

    async def add_completion(self, goal_id: uuid.UUID, completion_date: date) -> None:
        """Record a check-off. Idempotent at the DB level via the
        ``(goal_id, completion_date)`` unique constraint — the service wraps this
        in a savepoint so a duplicate tap is a no-op, not an error."""
        self.db.add(GoalCompletion(goal_id=goal_id, completion_date=completion_date))
        await self.db.flush()

    async def delete_completion(self, goal_id: uuid.UUID, completion_date: date) -> int:
        """Remove a check-off (un-log a mistaken tap). Returns rows deleted."""
        result = await self.db.execute(
            delete(GoalCompletion).where(
                GoalCompletion.goal_id == goal_id,
                GoalCompletion.completion_date == completion_date,
            )
        )
        await self.db.flush()
        return result.rowcount or 0
