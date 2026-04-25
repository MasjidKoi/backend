import uuid
from datetime import date

from geoalchemy2.functions import ST_DWithin
from sqlalchemy import Date, func, select

from app.models.user_checkin import UserCheckin
from app.repositories.base import BaseRepository


class UserCheckinRepository(BaseRepository[UserCheckin]):
    model = UserCheckin

    async def create(self, user_id: uuid.UUID, masjid_id: uuid.UUID) -> UserCheckin:
        checkin = UserCheckin(user_id=user_id, masjid_id=masjid_id)
        self.db.add(checkin)
        await self.db.flush()
        return checkin

    async def is_within_100m(self, masjid_id: uuid.UUID, user_point) -> bool:
        from app.models.masjid import Masjid as MasjidModel

        result = await self.db.execute(
            select(func.count())
            .select_from(MasjidModel)
            .where(
                MasjidModel.masjid_id == masjid_id,
                ST_DWithin(MasjidModel.location, user_point, 100),
            )
        )
        return result.scalar_one() > 0

    async def list_by_user(
        self, user_id: uuid.UUID, offset: int, limit: int
    ) -> tuple[list[tuple[UserCheckin, str | None]], int]:
        from app.models.masjid import Masjid as MasjidModel

        count_q = select(func.count()).where(UserCheckin.user_id == user_id)
        rows_q = (
            select(UserCheckin, MasjidModel.name)
            .outerjoin(MasjidModel, UserCheckin.masjid_id == MasjidModel.masjid_id)
            .where(UserCheckin.user_id == user_id)
            .order_by(UserCheckin.checked_in_at.desc())
            .offset(offset)
            .limit(limit)
        )
        total = (await self.db.execute(count_q)).scalar_one()
        rows = (await self.db.execute(rows_q)).all()
        return [(row[0], row[1]) for row in rows], total

    async def count_by_user(self, user_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).where(UserCheckin.user_id == user_id)
        )
        return result.scalar_one()

    async def get_distinct_dates(self, user_id: uuid.UUID) -> list[date]:
        date_col = func.date(UserCheckin.checked_in_at).cast(Date)
        result = await self.db.execute(
            select(date_col)
            .where(UserCheckin.user_id == user_id)
            .group_by(date_col)
            .order_by(date_col.desc())
            .limit(60)
        )
        return [row[0] for row in result.all()]
