from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.push_service import PushService


def get_push_service(db: AsyncSession = Depends(get_db)) -> PushService:
    return PushService(db)
