import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.models.masjid_review import MasjidReview
from app.repositories.masjid_repository import MasjidRepository
from app.repositories.masjid_review_repository import MasjidReviewRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.schemas.masjid_review import (
    LOW_STAR_MIN_BODY,
    MasjidReviewListResponse,
    MasjidReviewResponse,
    MasjidReviewUpsert,
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

    @staticmethod
    def _validate_low_star_body(rating: int, body: str | None) -> None:
        """1–2 star reviews must carry a short explanation; 3–5 may be stars-only."""
        if rating <= 2 and len((body or "").strip()) < LOW_STAR_MIN_BODY:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"A {rating}-star review needs at least {LOW_STAR_MIN_BODY} "
                    "characters explaining why."
                ),
            )

    async def upsert_review(
        self,
        masjid_id: uuid.UUID,
        user: CurrentUser,
        data: MasjidReviewUpsert,
    ) -> MasjidReviewResponse:
        """Idempotent 'put my review' — create the caller's single review or fully
        replace it, stamping `edited` on replacement."""
        self._validate_low_star_body(data.rating, data.body)
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Masjid not found"
            )

        user_uuid = uuid.UUID(str(user.user_id))
        profile = await self.profile_repo.get_by_user_id(user_uuid)
        display_name = profile.display_name if profile else None

        existing = await self.repo.get_by_user_masjid(user_uuid, masjid_id)
        if existing is not None:
            existing.rating = data.rating
            existing.body = data.body
            existing.reviewer_display_name = display_name
            existing.edited = True
            await self.repo.commit()
            # Reload — onupdate=func.now() expires updated_at after the flush.
            review = await self.repo.get_by_id(existing.review_id)
            return MasjidReviewResponse.model_validate(review)

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
        # The review's author may always delete their own review (PRD 07 story 45);
        # otherwise fall back to the masjid-admin / platform-admin moderation path.
        if review.user_id != uuid.UUID(str(user.user_id)):
            self._check_scope(user, masjid_id)
        await self.repo.delete(review)
        await self.repo.commit()
