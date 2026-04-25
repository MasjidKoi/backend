from sqlalchemy import func, select

from app.models.support_ticket import SupportTicket
from app.repositories.base import BaseRepository


class SupportTicketRepository(BaseRepository[SupportTicket]):
    model = SupportTicket

    async def create(self, **fields) -> SupportTicket:
        ticket = SupportTicket(**fields)
        self.db.add(ticket)
        await self.db.flush()
        return ticket

    async def list(
        self,
        status_filter: str | None,
        category_filter: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[SupportTicket], int]:
        base_where = []
        if status_filter:
            base_where.append(SupportTicket.status == status_filter)
        if category_filter:
            base_where.append(SupportTicket.category == category_filter)

        count_q = select(func.count())
        rows_q = select(SupportTicket)
        if base_where:
            count_q = count_q.where(*base_where)
            rows_q = rows_q.where(*base_where)

        count_result = await self.db.execute(count_q)
        total = count_result.scalar_one()

        rows_result = await self.db.execute(
            rows_q.order_by(SupportTicket.created_at.desc()).offset(offset).limit(limit)
        )
        return list(rows_result.scalars().all()), total

    async def update(self, ticket: SupportTicket, fields: dict) -> SupportTicket:
        for k, v in fields.items():
            setattr(ticket, k, v)
        await self.db.flush()
        return ticket
