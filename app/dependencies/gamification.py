from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.gamification_service import GamificationService


def get_gamification_service(db: AsyncSession = Depends(get_db)) -> GamificationService:
    return GamificationService(db)
