import uuid
from datetime import datetime

from sqlalchemy import select

from app.models.enums import RecurringScheduleStatus
from app.models.recurring_schedule import RecurringSchedule
from app.repositories.base import BaseRepository


class RecurringScheduleRepository(BaseRepository[RecurringSchedule]):
    model = RecurringSchedule

    async def list_by_user(self, user_id: uuid.UUID) -> list[RecurringSchedule]:
        rows = await self.db.execute(
            select(RecurringSchedule)
            .where(RecurringSchedule.user_id == user_id)
            .order_by(RecurringSchedule.created_at.desc())
        )
        return list(rows.scalars().all())

    async def get_owned(
        self, schedule_id: uuid.UUID, user_id: uuid.UUID
    ) -> RecurringSchedule | None:
        rows = await self.db.execute(
            select(RecurringSchedule).where(
                RecurringSchedule.schedule_id == schedule_id,
                RecurringSchedule.user_id == user_id,
            )
        )
        return rows.scalar_one_or_none()

    async def due(self, now: datetime, limit: int = 500) -> list[RecurringSchedule]:
        """Active schedules whose next nudge is due — the recurring-nudge sweep."""
        rows = await self.db.execute(
            select(RecurringSchedule)
            .where(
                RecurringSchedule.status == RecurringScheduleStatus.ACTIVE,
                RecurringSchedule.next_due_at <= now,
            )
            .order_by(RecurringSchedule.next_due_at.asc())
            .limit(limit)
        )
        return list(rows.scalars().all())
