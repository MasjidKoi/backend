"""Followed-masjids feed (PRD 07, gap #12).

One authenticated, type-parameterised, cursor-paginated endpoint joining the
caller's follows to either published announcements or upcoming events. Mute
mode does NOT affect the feed — it only silences push — so every followed
masjid's items appear here regardless of notification mode.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.core.security import CurrentUser
from app.dependencies.auth import get_current_user
from app.dependencies.feed import get_feed_service
from app.schemas.feed import FeedAnnouncementsResponse, FeedEventsResponse
from app.services.feed_service import FeedService

router = APIRouter(prefix="/users/me", tags=["feed"])


@router.get(
    "/feed",
    response_model=None,
    summary="Aggregated feed across followed masjids (announcements | events)",
)
async def get_feed(
    type: Literal["announcements", "events"] = Query(
        default="announcements", description="Which segment to return"
    ),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    user: CurrentUser = Depends(get_current_user),
    service: FeedService = Depends(get_feed_service),
) -> FeedAnnouncementsResponse | FeedEventsResponse:
    user_uuid = uuid.UUID(str(user.user_id))
    if type == "events":
        return await service.events(user_uuid, limit, cursor)
    return await service.announcements(user_uuid, limit, cursor)
