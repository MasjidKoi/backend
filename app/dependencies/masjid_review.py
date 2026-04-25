from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.masjid_review_service import MasjidReviewService


def get_masjid_review_service(
    db: AsyncSession = Depends(get_db),
) -> MasjidReviewService:
    return MasjidReviewService(db)
