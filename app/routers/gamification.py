import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.security import CurrentUser
from app.dependencies.auth import get_current_user
from app.dependencies.gamification import get_gamification_service
from app.schemas.gamification import (
    BadgeResponse,
    CheckInCreate,
    CheckInHistoryResponse,
    CheckInResponse,
    JournalEntryCreate,
    JournalEntryResponse,
    JournalListResponse,
    StreakResponse,
)
from app.services.gamification_service import GamificationService

masjid_router = APIRouter(prefix="/masjids", tags=["gamification"])
user_router = APIRouter(prefix="/users/me", tags=["gamification"])


@masjid_router.post(
    "/{masjid_id}/checkin",
    response_model=CheckInResponse,
    status_code=201,
    summary="Check in at a masjid (within 100 m)",
)
async def checkin(
    masjid_id: uuid.UUID,
    body: CheckInCreate,
    user: CurrentUser = Depends(get_current_user),
    service: GamificationService = Depends(get_gamification_service),
) -> CheckInResponse:
    return await service.checkin(masjid_id, body, user)


@user_router.get(
    "/checkins",
    response_model=CheckInHistoryResponse,
    summary="List check-in history",
)
async def list_checkins(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    service: GamificationService = Depends(get_gamification_service),
) -> CheckInHistoryResponse:
    return await service.list_checkins(user, page, page_size)


@user_router.get(
    "/badges",
    response_model=list[BadgeResponse],
    summary="List earned badges",
)
async def list_badges(
    user: CurrentUser = Depends(get_current_user),
    service: GamificationService = Depends(get_gamification_service),
) -> list[BadgeResponse]:
    return await service.list_badges(user)


@user_router.get(
    "/streak",
    response_model=StreakResponse,
    summary="Current check-in streak and total",
)
async def get_streak(
    user: CurrentUser = Depends(get_current_user),
    service: GamificationService = Depends(get_gamification_service),
) -> StreakResponse:
    return await service.get_streak(user)


@user_router.get(
    "/journal",
    response_model=JournalListResponse,
    summary="List journal entries",
)
async def list_journal(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
    service: GamificationService = Depends(get_gamification_service),
) -> JournalListResponse:
    return await service.list_journal(user, page, page_size, date_from, date_to)


@user_router.post(
    "/journal",
    response_model=JournalEntryResponse,
    summary="Create or update a journal entry for a given date",
)
async def upsert_journal(
    body: JournalEntryCreate,
    user: CurrentUser = Depends(get_current_user),
    service: GamificationService = Depends(get_gamification_service),
) -> JournalEntryResponse:
    return await service.upsert_journal(body, user)
