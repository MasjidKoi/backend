from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select

from app.models.push_receipt import PushReceipt
from app.repositories.base import BaseRepository


class PushReceiptRepository(BaseRepository[PushReceipt]):
    model = PushReceipt

    async def create_many(self, pairs: Sequence[tuple[str, str]]) -> int:
        """Record ``(receipt_id, token)`` rows for sends awaiting a receipt poll."""
        rows = [PushReceipt(receipt_id=rid, token=token) for rid, token in pairs]
        if not rows:
            return 0
        self.db.add_all(rows)
        await self.db.flush()
        return len(rows)

    async def get_due(self, older_than: datetime, limit: int) -> list[PushReceipt]:
        """Oldest-first receipts created before ``older_than`` — the poll batch."""
        result = await self.db.execute(
            select(PushReceipt)
            .where(PushReceipt.created_at < older_than)
            .order_by(PushReceipt.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def delete_by_ids(self, receipt_ids: Sequence[str]) -> int:
        if not receipt_ids:
            return 0
        result = await self.db.execute(
            delete(PushReceipt).where(PushReceipt.receipt_id.in_(list(receipt_ids)))
        )
        await self.db.flush()
        return result.rowcount or 0
