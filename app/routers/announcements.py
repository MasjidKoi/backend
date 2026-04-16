"""
Announcements router.

Shares /masjids prefix — all routes are sub-resources of a masjid.
Public: list published, get single published.
Admin: create, update, publish, delete (masjid_admin scoped to own masjid).
"""

import uuid

from fastapi import APIRouter, Depends, Query, status

from app.core.security import CurrentUser
from app.dependencies.announcement import get_announcement_service
from app.dependencies.auth import require_masjid_admin
from app.schemas.announcement import (
    AnnouncementCreate,
    AnnouncementListResponse,
    AnnouncementResponse,
    AnnouncementUpdate,
)

from app.services.announcement_service import AnnouncementService

router = APIRouter(prefix="/masjids", tags=["announcements"])


@router.post(
    "/{masjid_id}/announcements",
    response_model=AnnouncementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create announcement — draft or published (masjid_admin)",
)
async def create_announcement(
    masjid_id: uuid.UUID,
    body: AnnouncementCreate,
    user: CurrentUser = Depends(require_masjid_admin),
    service: AnnouncementService = Depends(get_announcement_service),
) -> AnnouncementResponse:
    return await service.create(masjid_id, body, user)


@router.get(
    "/{masjid_id}/announcements/admin",
    response_model=AnnouncementListResponse,
    summary="List ALL announcements including drafts (masjid_admin)",
)
async def list_announcements_admin(
    masjid_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    user: CurrentUser = Depends(require_masjid_admin),
    service: AnnouncementService = Depends(get_announcement_service),
) -> AnnouncementListResponse:
    return await service.list_admin(masjid_id, page, page_size, user)


@router.get(
    "/{masjid_id}/announcements",
    response_model=AnnouncementListResponse,
    summary="List published announcements (public)",
)
async def list_announcements(
    masjid_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: AnnouncementService = Depends(get_announcement_service),
) -> AnnouncementListResponse:
    return await service.list_published(masjid_id, page, page_size)


@router.get(
    "/{masjid_id}/announcements/{announcement_id}",
    response_model=AnnouncementResponse,
    summary="Get a single published announcement (public)",
)
async def get_announcement(
    masjid_id: uuid.UUID,
    announcement_id: uuid.UUID,
    service: AnnouncementService = Depends(get_announcement_service),
) -> AnnouncementResponse:
    return await service.get_by_id(masjid_id, announcement_id)


@router.patch(
    "/{masjid_id}/announcements/{announcement_id}",
    response_model=AnnouncementResponse,
    summary="Update announcement title/body (masjid_admin)",
)
async def update_announcement(
    masjid_id: uuid.UUID,
    announcement_id: uuid.UUID,
    body: AnnouncementUpdate,
    user: CurrentUser = Depends(require_masjid_admin),
    service: AnnouncementService = Depends(get_announcement_service),
) -> AnnouncementResponse:
    return await service.update(masjid_id, announcement_id, body, user)


@router.post(
    "/{masjid_id}/announcements/{announcement_id}/publish",
    response_model=AnnouncementResponse,
    summary="Publish a draft announcement (masjid_admin)",
)
async def publish_announcement(
    masjid_id: uuid.UUID,
    announcement_id: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: AnnouncementService = Depends(get_announcement_service),
) -> AnnouncementResponse:
    return await service.publish(masjid_id, announcement_id, user)


@router.delete(
    "/{masjid_id}/announcements/{announcement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an announcement (masjid_admin)",
)
async def delete_announcement(
    masjid_id: uuid.UUID,
    announcement_id: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: AnnouncementService = Depends(get_announcement_service),
) -> None:
    await service.delete(masjid_id, announcement_id, user)
