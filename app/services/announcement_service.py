import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.repositories.announcement_repository import AnnouncementRepository
from app.repositories.masjid_repository import MasjidRepository
from app.schemas.announcement import (
    AnnouncementCreate,
    AnnouncementListResponse,
    AnnouncementPlatformListResponse,
    AnnouncementResponse,
    AnnouncementUpdate,
    AnnouncementWithMasjidResponse,
)


class AnnouncementService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = AnnouncementRepository(db)
        self.masjid_repo = MasjidRepository(db)

    def _check_scope(self, user: CurrentUser, masjid_id: uuid.UUID) -> None:
        if user.is_platform_admin:
            return
        if user.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to your own masjid",
            )

    def _to_response(self, ann) -> AnnouncementResponse:
        return AnnouncementResponse(
            announcement_id=ann.announcement_id,
            masjid_id=ann.masjid_id,
            title=ann.title,
            body=ann.body,
            is_published=ann.is_published,
            published_at=ann.published_at,
            posted_by_email=ann.posted_by_email,
            created_at=ann.created_at,
            updated_at=ann.updated_at,
        )

    async def _get_masjid_or_404(self, masjid_id: uuid.UUID) -> None:
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Masjid not found"
            )

    async def _get_ann_or_404(self, announcement_id: uuid.UUID, masjid_id: uuid.UUID):
        ann = await self.repo.get_by_id_and_masjid(announcement_id, masjid_id)
        if not ann:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Announcement not found",
            )
        return ann

    # ── Public reads ───────────────────────────────────────────────────────────

    async def list_published(
        self, masjid_id: uuid.UUID, page: int, page_size: int
    ) -> AnnouncementListResponse:
        await self._get_masjid_or_404(masjid_id)
        rows, total = await self.repo.get_published_by_masjid(
            masjid_id, offset=(page - 1) * page_size, limit=page_size
        )
        return AnnouncementListResponse(
            items=[self._to_response(a) for a in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_by_id(
        self, masjid_id: uuid.UUID, announcement_id: uuid.UUID
    ) -> AnnouncementResponse:
        ann = await self._get_ann_or_404(announcement_id, masjid_id)
        if not ann.is_published:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Announcement not found",
            )
        return self._to_response(ann)

    # ── Admin reads ────────────────────────────────────────────────────────────

    async def list_admin(
        self, masjid_id: uuid.UUID, page: int, page_size: int, user: CurrentUser
    ) -> AnnouncementListResponse:
        """Admin listing — includes drafts. Scoped to own masjid."""
        self._check_scope(user, masjid_id)
        await self._get_masjid_or_404(masjid_id)
        rows, total = await self.repo.get_all_by_masjid(
            masjid_id, offset=(page - 1) * page_size, limit=page_size
        )
        return AnnouncementListResponse(
            items=[self._to_response(a) for a in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def list_platform(
        self,
        page: int,
        page_size: int,
        masjid_id: uuid.UUID | None = None,
    ) -> AnnouncementPlatformListResponse:
        """Platform admin — all announcements across all masjids with masjid name."""
        rows, total = await self.repo.get_all_platform(
            offset=(page - 1) * page_size, limit=page_size, masjid_id=masjid_id
        )
        items = [
            AnnouncementWithMasjidResponse(
                **self._to_response(ann).model_dump(),
                masjid_name=name,
            )
            for ann, name in rows
        ]
        return AnnouncementPlatformListResponse(
            items=items, total=total, page=page, page_size=page_size
        )

    # ── Admin writes ───────────────────────────────────────────────────────────

    async def create(
        self,
        masjid_id: uuid.UUID,
        data: AnnouncementCreate,
        user: CurrentUser,
    ) -> AnnouncementResponse:
        self._check_scope(user, masjid_id)
        await self._get_masjid_or_404(masjid_id)
        ann = await self.repo.create(
            masjid_id=masjid_id,
            title=data.title,
            body=data.body,
            posted_by_id=user.user_id,
            posted_by_email=user.email,
            publish=data.publish,
        )
        await self.repo.commit()
        return self._to_response(ann)

    async def update(
        self,
        masjid_id: uuid.UUID,
        announcement_id: uuid.UUID,
        data: AnnouncementUpdate,
        user: CurrentUser,
    ) -> AnnouncementResponse:
        self._check_scope(user, masjid_id)
        ann = await self._get_ann_or_404(announcement_id, masjid_id)
        fields = data.model_dump(exclude_unset=True)
        await self.repo.update(ann, fields)
        await self.repo.commit()
        # Reload after commit — onupdate=func.now() expires updated_at after flush,
        # and lazy-loading is forbidden in async context (MissingGreenlet).
        ann = await self._get_ann_or_404(announcement_id, masjid_id)
        return self._to_response(ann)

    async def publish(
        self,
        masjid_id: uuid.UUID,
        announcement_id: uuid.UUID,
        user: CurrentUser,
    ) -> AnnouncementResponse:
        self._check_scope(user, masjid_id)
        ann = await self._get_ann_or_404(announcement_id, masjid_id)
        if ann.is_published:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Announcement is already published",
            )
        await self.repo.publish(ann)
        await self.repo.commit()
        ann = await self._get_ann_or_404(announcement_id, masjid_id)
        return self._to_response(ann)

    async def delete(
        self,
        masjid_id: uuid.UUID,
        announcement_id: uuid.UUID,
        user: CurrentUser,
    ) -> None:
        self._check_scope(user, masjid_id)
        ann = await self._get_ann_or_404(announcement_id, masjid_id)
        await self.repo.delete(ann)
        await self.repo.commit()
