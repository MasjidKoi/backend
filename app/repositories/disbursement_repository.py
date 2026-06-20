import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.models.disbursement import Disbursement
from app.repositories.base import BaseRepository


class DisbursementRepository(BaseRepository[Disbursement]):
    model = Disbursement

    async def sum_for_masjid(self, masjid_id: uuid.UUID) -> Decimal:
        """Total recorded payouts for a masjid — the debit side of the derived
        balance (net completed donations − disbursements)."""
        result = await self.db.execute(
            select(func.coalesce(func.sum(Disbursement.amount), 0)).where(
                Disbursement.masjid_id == masjid_id
            )
        )
        return Decimal(result.scalar_one())

    async def total_by_masjid(self) -> dict[uuid.UUID, Decimal]:
        """Recorded payouts grouped by masjid — debit side for the platform-wide
        balances view."""
        rows = await self.db.execute(
            select(
                Disbursement.masjid_id, func.coalesce(func.sum(Disbursement.amount), 0)
            ).group_by(Disbursement.masjid_id)
        )
        return {mid: Decimal(total) for mid, total in rows.all()}

    async def list_for_masjid(
        self, masjid_id: uuid.UUID, offset: int, limit: int
    ) -> tuple[list[Disbursement], int]:
        base = [Disbursement.masjid_id == masjid_id]
        total = (await self.db.execute(select(func.count()).where(*base))).scalar_one()
        rows = (
            (
                await self.db.execute(
                    select(Disbursement)
                    .where(*base)
                    .order_by(Disbursement.disbursed_on.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return list(rows), total
