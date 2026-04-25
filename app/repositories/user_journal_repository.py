import uuid
from datetime import date

from sqlalchemy import func, select

from app.models.user_journal_entry import UserJournalEntry
from app.repositories.base import BaseRepository


class UserJournalRepository(BaseRepository[UserJournalEntry]):
    model = UserJournalEntry

    async def get_by_user_date(
        self, user_id: uuid.UUID, entry_date: date
    ) -> UserJournalEntry | None:
        result = await self.db.execute(
            select(UserJournalEntry).where(
                UserJournalEntry.user_id == user_id,
                UserJournalEntry.entry_date == entry_date,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        offset: int,
        limit: int,
        date_from: date | None,
        date_to: date | None,
    ) -> tuple[list[UserJournalEntry], int]:
        filters = [UserJournalEntry.user_id == user_id]
        if date_from:
            filters.append(UserJournalEntry.entry_date >= date_from)
        if date_to:
            filters.append(UserJournalEntry.entry_date <= date_to)

        count_q = select(func.count()).where(*filters)
        rows_q = (
            select(UserJournalEntry)
            .where(*filters)
            .order_by(UserJournalEntry.entry_date.desc())
            .offset(offset)
            .limit(limit)
        )
        total = (await self.db.execute(count_q)).scalar_one()
        rows = list((await self.db.execute(rows_q)).scalars().all())
        return rows, total
