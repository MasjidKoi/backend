from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.goal_service import GoalService


def get_goal_service(db: AsyncSession = Depends(get_db)) -> GoalService:
    return GoalService(db)
