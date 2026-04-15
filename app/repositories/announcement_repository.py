import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models.announcement import Announcement
from app.repositories.base import BaseRepository


class AnnouncementRepository(BaseRepository[Announcement]):
    model = Announcement

    async def get_published_by_masjid(
        self,
        masjid_id: uuid.UUID,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Announcement], int]:
        base = (
            select(Announcement)
            .where(
                Announcement.masjid_id == masjid_id,
                Announcement.is_published == True,  # noqa: E712
            )
        )
        count = (await self.db.execute(
            select(func.count()).select_from(base.subquery())
        )).scalar_one()
        rows = list((await self.db.execute(
            base.order_by(Announcement.published_at.desc()).offset(offset).limit(limit)
        )).scalars().all())
        return rows, count

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

    async def create(
        self,
        *,
        masjid_id: uuid.UUID,
        title: str,
        body: str,
        posted_by_id: uuid.UUID,
        posted_by_email: str | None,
        publish: bool = False,
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
