from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.masjid_submission_service import MasjidSubmissionService


def get_masjid_submission_service(
    db: AsyncSession = Depends(get_db),
) -> MasjidSubmissionService:
    return MasjidSubmissionService(db)
