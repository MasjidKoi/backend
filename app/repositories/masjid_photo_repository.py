import uuid
from datetime import datetime

from sqlalchemy import delete, func, or_, select, update

from app.models.enums import PhotoModerationStatus, PhotoSource
from app.models.masjid import MasjidPhoto
from app.repositories.base import BaseRepository


class MasjidPhotoRepository(BaseRepository[MasjidPhoto]):
    model = MasjidPhoto

    # ── Admin gallery (source='admin') ───────────────────────────────────────
    # All admin-gallery reads/writes are scoped to source='admin' so community
    # submissions never count against the cap, become cover, or get reordered.

    async def list_by_masjid(self, masjid_id: uuid.UUID) -> list[MasjidPhoto]:
        result = await self.db.execute(
            select(MasjidPhoto)
            .where(MasjidPhoto.masjid_id == masjid_id)
            .where(MasjidPhoto.source == PhotoSource.ADMIN)
            .order_by(MasjidPhoto.display_order)
        )
        return list(result.scalars().all())

    async def get_by_id(self, photo_id: uuid.UUID) -> MasjidPhoto | None:
        result = await self.db.execute(
            select(MasjidPhoto).where(MasjidPhoto.photo_id == photo_id)
        )
        return result.scalar_one_or_none()

    async def count_by_masjid(self, masjid_id: uuid.UUID) -> int:
        """Admin-gallery photo count — backs the per-masjid admin cap."""
        result = await self.db.execute(
            select(func.count())
            .select_from(MasjidPhoto)
            .where(MasjidPhoto.masjid_id == masjid_id)
            .where(MasjidPhoto.source == PhotoSource.ADMIN)
        )
        return result.scalar_one()

    async def create(
        self,
        *,
        masjid_id: uuid.UUID,
        url: str,
        is_cover: bool,
        display_order: int,
        source: str = PhotoSource.ADMIN,
        status: str = PhotoModerationStatus.APPROVED,
        uploaded_by: uuid.UUID | None = None,
    ) -> MasjidPhoto:
        photo = MasjidPhoto(
            masjid_id=masjid_id,
            url=url,
            is_cover=is_cover,
            display_order=display_order,
            source=source,
            status=status,
            uploaded_by=uploaded_by,
        )
        self.db.add(photo)
        await self.db.flush()
        return photo

    async def set_cover(self, masjid_id: uuid.UUID, photo_id: uuid.UUID) -> None:
        # Cover lives only in the admin gallery — only clear/set admin rows.
        await self.db.execute(
            update(MasjidPhoto)
            .where(MasjidPhoto.masjid_id == masjid_id)
            .where(MasjidPhoto.source == PhotoSource.ADMIN)
            .values(is_cover=False)
        )
        await self.db.execute(
            update(MasjidPhoto)
            .where(MasjidPhoto.photo_id == photo_id)
            .values(is_cover=True)
        )
        await self.db.flush()

    async def delete_photo(self, photo: MasjidPhoto) -> None:
        await self.db.execute(
            delete(MasjidPhoto).where(MasjidPhoto.photo_id == photo.photo_id)
        )
        await self.db.flush()

    async def reorder(
        self, masjid_id: uuid.UUID, ordered_photo_ids: list[uuid.UUID]
    ) -> None:
        for i, photo_id in enumerate(ordered_photo_ids):
            await self.db.execute(
                update(MasjidPhoto)
                .where(
                    MasjidPhoto.photo_id == photo_id,
                    MasjidPhoto.masjid_id == masjid_id,
                    MasjidPhoto.source == PhotoSource.ADMIN,
                )
                .values(display_order=i)
            )
        await self.db.flush()

    # ── Community submissions (source='community') ───────────────────────────

    async def count_community_by_user_since(
        self, user_id: uuid.UUID, since: datetime
    ) -> int:
        """Community photos this user has submitted since `since` (per-user/day cap)."""
        result = await self.db.execute(
            select(func.count())
            .select_from(MasjidPhoto)
            .where(MasjidPhoto.uploaded_by == user_id)
            .where(MasjidPhoto.source == PhotoSource.COMMUNITY)
            .where(MasjidPhoto.created_at >= since)
        )
        return result.scalar_one()

    async def count_approved_community_by_user(self, user_id: uuid.UUID) -> int:
        """Approved community photos uploaded by this user, for Community Pillar."""
        result = await self.db.execute(
            select(func.count())
            .select_from(MasjidPhoto)
            .where(MasjidPhoto.uploaded_by == user_id)
            .where(MasjidPhoto.source == PhotoSource.COMMUNITY)
            .where(MasjidPhoto.status == PhotoModerationStatus.APPROVED)
        )
        return result.scalar_one()

    async def count_community_by_user_masjid_since(
        self, user_id: uuid.UUID, masjid_id: uuid.UUID, since: datetime
    ) -> int:
        """Community photos this user has submitted to one masjid since `since`."""
        result = await self.db.execute(
            select(func.count())
            .select_from(MasjidPhoto)
            .where(MasjidPhoto.uploaded_by == user_id)
            .where(MasjidPhoto.masjid_id == masjid_id)
            .where(MasjidPhoto.source == PhotoSource.COMMUNITY)
            .where(MasjidPhoto.created_at >= since)
        )
        return result.scalar_one()

    async def list_approved_community(
        self, masjid_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[list[MasjidPhoto], int]:
        """Public listing of approved community photos for one masjid."""
        base = (
            select(MasjidPhoto)
            .where(MasjidPhoto.masjid_id == masjid_id)
            .where(MasjidPhoto.source == PhotoSource.COMMUNITY)
            .where(MasjidPhoto.status == PhotoModerationStatus.APPROVED)
        )
        total: int = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = list(
            (
                await self.db.execute(
                    base.order_by(MasjidPhoto.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return rows, total

    async def list_community_for_masjid_moderation(
        self,
        masjid_id: uuid.UUID,
        *,
        status: str | None,
        offset: int,
        limit: int,
        ngo_overdue_before: datetime | None = None,
    ) -> tuple[list[MasjidPhoto], int]:
        """Moderation queue for a masjid (any/filtered community status).

        ``ngo_overdue_before`` restricts the queue to the NGO-visible slice of a
        *claimed* masjid (Gap #10): pending rows only when overdue past the SLA,
        plus all resolved history. Left unset for the masjid admin's own queue.
        """
        base = (
            select(MasjidPhoto)
            .where(MasjidPhoto.masjid_id == masjid_id)
            .where(MasjidPhoto.source == PhotoSource.COMMUNITY)
        )
        if status:
            base = base.where(MasjidPhoto.status == status)
        if ngo_overdue_before is not None:
            base = base.where(
                or_(
                    MasjidPhoto.status != PhotoModerationStatus.PENDING,
                    MasjidPhoto.created_at <= ngo_overdue_before,
                )
            )
        total: int = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = list(
            (
                await self.db.execute(
                    base.order_by(MasjidPhoto.created_at.asc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return rows, total

    async def list_community_for_user(
        self, user_id: uuid.UUID, limit: int = 50
    ) -> list[MasjidPhoto]:
        """The submitter's own community photos (GET /me/photo-submissions)."""
        result = await self.db.execute(
            select(MasjidPhoto)
            .where(MasjidPhoto.uploaded_by == user_id)
            .where(MasjidPhoto.source == PhotoSource.COMMUNITY)
            .order_by(MasjidPhoto.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def set_status(self, photo: MasjidPhoto, new_status: str) -> MasjidPhoto:
        photo.status = new_status
        await self.db.flush()
        return photo
