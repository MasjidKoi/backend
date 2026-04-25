import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models.announcement import Announcement
from app.models.masjid import Masjid
from app.repositories.base import BaseRepository


class AnnouncementRepository(BaseRepository[Announcement]):
    model = Announcement

    async def get_published_by_masjid(
        self,
        masjid_id: uuid.UUID,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Announcement], int]:
        base = select(Announcement).where(
            Announcement.masjid_id == masjid_id,
            Announcement.is_published == True,  # noqa: E712
        )
        count = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = list(
            (
                await self.db.execute(
                    base.order_by(Announcement.published_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return rows, count

    async def get_counts(self) -> tuple[int, int]:
        """Returns (total, published) announcement counts across all masjids."""
        result = await self.db.execute(
            select(
                func.count().label("total"),
                func.count()
                .filter(Announcement.is_published == True)
                .label("published"),  # noqa: E712
            ).select_from(Announcement)
        )
        row = result.one()
        return row.total, row.published

    async def get_all_by_masjid(
        self,
        masjid_id: uuid.UUID,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Announcement], int]:
        """Admin listing — all announcements (drafts + published) for one masjid."""
        base = select(Announcement).where(Announcement.masjid_id == masjid_id)
        count = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = list(
            (
                await self.db.execute(
                    base.order_by(Announcement.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return rows, count

    async def get_all_platform(
        self,
        offset: int = 0,
        limit: int = 20,
        masjid_id: uuid.UUID | None = None,
    ) -> tuple[list[tuple[Announcement, str]], int]:
        """Platform admin — cross-masjid listing with masjid name via JOIN."""
        count_stmt = select(Announcement).join(
            Masjid, Announcement.masjid_id == Masjid.masjid_id
        )
        data_stmt = select(Announcement, Masjid.name.label("masjid_name")).join(
            Masjid, Announcement.masjid_id == Masjid.masjid_id
        )
        if masjid_id is not None:
            count_stmt = count_stmt.where(Announcement.masjid_id == masjid_id)
            data_stmt = data_stmt.where(Announcement.masjid_id == masjid_id)
        count = (
            await self.db.execute(
                select(func.count()).select_from(count_stmt.subquery())
            )
        ).scalar_one()
        rows = list(
            (
                await self.db.execute(
                    data_stmt.order_by(Announcement.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        return [(r.Announcement, r.masjid_name) for r in rows], count

    async def get_by_id_and_masjid(
        self, announcement_id: uuid.UUID, masjid_id: uuid.UUID
    ) -> Announcement | None:
        result = await self.db.execute(
            select(Announcement).where(
                Announcement.announcement_id == announcement_id,
                Announcement.masjid_id == masjid_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_overdue_scheduled(self) -> list[Announcement]:
        """Return unpublished announcements whose scheduled_at has passed."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(Announcement).where(
                Announcement.is_published == False,  # noqa: E712
                Announcement.scheduled_at.is_not(None),
                Announcement.scheduled_at <= now,
            )
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        masjid_id: uuid.UUID,
        title: str,
        body: str,
        posted_by_id: uuid.UUID,
        posted_by_email: str | None,
        publish: bool = False,
        scheduled_at: datetime | None = None,
    ) -> Announcement:
        now = datetime.now(timezone.utc) if publish else None
        ann = Announcement(
            masjid_id=masjid_id,
            title=title,
            body=body,
            posted_by_id=posted_by_id,
            posted_by_email=posted_by_email,
            is_published=publish,
            published_at=now,
            scheduled_at=None if publish else scheduled_at,
        )
        self.db.add(ann)
        await self.db.flush()
        return ann

    async def update(self, ann: Announcement, fields: dict) -> Announcement:
        for k, v in fields.items():
            setattr(ann, k, v)
        await self.db.flush()
        return ann

    async def publish(self, ann: Announcement) -> Announcement:
        ann.is_published = True
        ann.published_at = datetime.now(timezone.utc)
        await self.db.flush()
        return ann

    async def delete(self, ann: Announcement) -> None:
        await self.db.delete(ann)
        await self.db.flush()
