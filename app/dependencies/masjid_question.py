from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.masjid_question_service import MasjidQuestionService


def get_masjid_question_service(
    db: AsyncSession = Depends(get_db),
) -> MasjidQuestionService:
    return MasjidQuestionService(db)
