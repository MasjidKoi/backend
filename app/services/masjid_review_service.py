import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.models.masjid_review import MasjidReview
from app.repositories.masjid_repository import MasjidRepository
from app.repositories.masjid_review_repository import MasjidReviewRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.schemas.masjid_review import (
    MasjidReviewCreate,
    MasjidReviewListResponse,
    MasjidReviewResponse,
)


class MasjidReviewService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = MasjidReviewRepository(db)
        self.masjid_repo = MasjidRepository(db)
        self.profile_repo = UserProfileRepository(db)

    def _check_scope(self, user: CurrentUser, masjid_id: uuid.UUID) -> None:
        if user.is_platform_admin:
            return
        if user.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to your own masjid",
            )

    async def submit_review(
        self,
        masjid_id: uuid.UUID,
        user: CurrentUser,
        data: MasjidReviewCreate,
    ) -> MasjidReviewResponse:
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Masjid not found"
            )

        user_uuid = uuid.UUID(str(user.user_id))
        existing = await self.repo.get_by_user_masjid(user_uuid, masjid_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You have already reviewed this masjid",
            )

        profile = await self.profile_repo.get_by_user_id(user_uuid)
        display_name = profile.display_name if profile else None

        review = MasjidReview(
            masjid_id=masjid_id,
            user_id=user_uuid,
            rating=data.rating,
            body=data.body,
            reviewer_display_name=display_name,
        )
        await self.repo.add(review)
        await self.repo.commit()
        return MasjidReviewResponse.model_validate(review)

    async def list_reviews(
        self,
        masjid_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> MasjidReviewListResponse:
        offset = (page - 1) * page_size
        rows, total = await self.repo.list_by_masjid(masjid_id, offset, page_size)
        avg = await self.repo.get_average_rating(masjid_id)
        return MasjidReviewListResponse(
            items=[MasjidReviewResponse.model_validate(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
            average_rating=avg,
        )

    async def delete_review(
        self,
        masjid_id: uuid.UUID,
        review_id: uuid.UUID,
        user: CurrentUser,
    ) -> None:
        review = await self.repo.get_by_id(review_id)
        if not review or review.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Review not found"
            )
        self._check_scope(user, masjid_id)
        await self.repo.delete(review)
        await self.repo.commit()
