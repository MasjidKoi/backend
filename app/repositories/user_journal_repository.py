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

    async def get_day_records(
        self, user_id: uuid.UUID, since: date | None = None
    ) -> list[UserJournalEntry]:
        """Entries ordered by date ascending — the StreakEngine/BadgeEngine fold
        input. Bounded by ``since`` so a long-lived account stays cheap."""
        filters = [UserJournalEntry.user_id == user_id]
        if since is not None:
            filters.append(UserJournalEntry.entry_date >= since)
        result = await self.db.execute(
            select(UserJournalEntry)
            .where(*filters)
            .order_by(UserJournalEntry.entry_date.asc())
        )
        return list(result.scalars().all())

    async def sum_quran_amount(
        self,
        user_id: uuid.UUID,
        unit: str,
        date_from: date,
        date_to: date,
    ) -> int:
        """Total logged Qur'an in one unit over a date window — the journal-fed
        source for a Qur'an-quantity goal's progress (PRD 08 US #38).

        Only entries whose unit *matches* the goal's are summed; cross-unit
        conversion is a client concern handled at unit-switch, never here."""
        result = await self.db.execute(
            select(func.coalesce(func.sum(UserJournalEntry.quran_amount), 0)).where(
                UserJournalEntry.user_id == user_id,
                UserJournalEntry.quran_unit == unit,
                UserJournalEntry.entry_date >= date_from,
                UserJournalEntry.entry_date <= date_to,
            )
        )
        return int(result.scalar_one())

    async def quran_daily_sums(
        self,
        user_id: uuid.UUID,
        date_from: date,
        date_to: date,
    ) -> dict[tuple[str, date], int]:
        """Per ``(unit, day)`` Qur'an totals over a window — the batched source
        for computing many Qur'an-quantity goals' progress in one query instead
        of an N+1 of ``sum_quran_amount`` per goal. A goal's total is the sum of
        the buckets whose unit matches and whose day falls in its date window."""
        result = await self.db.execute(
            select(
                UserJournalEntry.quran_unit,
                UserJournalEntry.entry_date,
                func.coalesce(func.sum(UserJournalEntry.quran_amount), 0),
            )
            .where(
                UserJournalEntry.user_id == user_id,
                UserJournalEntry.quran_unit.is_not(None),
                UserJournalEntry.entry_date >= date_from,
                UserJournalEntry.entry_date <= date_to,
            )
            .group_by(UserJournalEntry.quran_unit, UserJournalEntry.entry_date)
        )
        return {
            (unit, entry_date): int(total) for unit, entry_date, total in result.all()
        }

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
