import uuid

from sqlalchemy import func, select

from app.models.masjid_review import MasjidReview
from app.repositories.base import BaseRepository


class MasjidReviewRepository(BaseRepository[MasjidReview]):
    model = MasjidReview

    async def get_by_user_masjid(
        self, user_id: uuid.UUID, masjid_id: uuid.UUID
    ) -> MasjidReview | None:
        result = await self.db.execute(
            select(MasjidReview).where(
                MasjidReview.user_id == user_id,
                MasjidReview.masjid_id == masjid_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_masjid(
        self, masjid_id: uuid.UUID, offset: int, limit: int
    ) -> tuple[list[MasjidReview], int]:
        count_result = await self.db.execute(
            select(func.count()).where(MasjidReview.masjid_id == masjid_id)
        )
        total = count_result.scalar_one()
        rows_result = await self.db.execute(
            select(MasjidReview)
            .where(MasjidReview.masjid_id == masjid_id)
            .order_by(MasjidReview.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(rows_result.scalars().all()), total

    async def get_average_rating(self, masjid_id: uuid.UUID) -> float | None:
        result = await self.db.execute(
            select(func.avg(MasjidReview.rating)).where(
                MasjidReview.masjid_id == masjid_id
            )
        )
        avg = result.scalar_one_or_none()
        return float(avg) if avg is not None else None

    async def delete(self, review: MasjidReview) -> None:
        await self.db.delete(review)
        await self.db.flush()
