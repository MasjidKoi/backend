import uuid

from sqlalchemy import and_, func, select

from app.models.enums import MasjidSubmissionStatus
from app.models.masjid_submission import MasjidSubmission
from app.repositories.base import BaseRepository


class MasjidSubmissionRepository(BaseRepository[MasjidSubmission]):
    model = MasjidSubmission

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        name: str,
        latitude: float,
        longitude: float,
        address: str | None,
        photo_key: str | None,
    ) -> MasjidSubmission:
        submission = MasjidSubmission(
            user_id=user_id,
            name=name,
            latitude=latitude,
            longitude=longitude,
            address=address,
            photo_key=photo_key,
        )
        self.db.add(submission)
        await self.db.flush()
        return submission

    async def get_by_id(self, submission_id: uuid.UUID) -> MasjidSubmission | None:
        result = await self.db.execute(
            select(MasjidSubmission).where(
                MasjidSubmission.submission_id == submission_id
            )
        )
        return result.scalar_one_or_none()

    async def count_pending_for_user(self, user_id: uuid.UUID) -> int:
        """Backs the per-user pending cap — uses the (user_id, status) index."""
        result = await self.db.execute(
            select(func.count())
            .select_from(MasjidSubmission)
            .where(MasjidSubmission.user_id == user_id)
            .where(MasjidSubmission.status == MasjidSubmissionStatus.PENDING)
        )
        return result.scalar_one()

    async def list_for_user(
        self, user_id: uuid.UUID, limit: int = 50
    ) -> list[MasjidSubmission]:
        result = await self.db.execute(
            select(MasjidSubmission)
            .where(MasjidSubmission.user_id == user_id)
            .order_by(MasjidSubmission.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_for_admin(
        self,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[MasjidSubmission], int]:
        filters = []
        if status:
            filters.append(MasjidSubmission.status == status)
        where = and_(*filters) if filters else None

        count_stmt = select(func.count()).select_from(MasjidSubmission)
        data_stmt = select(MasjidSubmission).order_by(
            MasjidSubmission.created_at.desc()
        )
        if where is not None:
            count_stmt = count_stmt.where(where)
            data_stmt = data_stmt.where(where)

        total: int = (await self.db.execute(count_stmt)).scalar_one()
        rows = list(
            (await self.db.execute(data_stmt.offset(offset).limit(limit)))
            .scalars()
            .all()
        )
        return rows, total

    async def set_status(
        self,
        submission: MasjidSubmission,
        new_status: str,
        approved_masjid_id: uuid.UUID | None = None,
    ) -> MasjidSubmission:
        submission.status = new_status
        if approved_masjid_id is not None:
            submission.approved_masjid_id = approved_masjid_id
        await self.db.flush()
        return submission
