from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.feed_service import FeedService


def get_feed_service(db: AsyncSession = Depends(get_db)) -> FeedService:
    return FeedService(db)
