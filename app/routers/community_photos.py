import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile, status

from app.core.rate_limit import make_rate_limiter
from app.core.security import CurrentUser
from app.dependencies.auth import get_current_user, require_masjid_admin
from app.dependencies.community_photo import get_community_photo_service
from app.dependencies.storage import get_storage_service
from app.schemas.community_photo import (
    CommunityPhotoModerationListResponse,
    CommunityPhotoModerationResponse,
    CommunityPhotoPublicListResponse,
    CommunityPhotoSubmissionResponse,
)
from app.services.community_photo_service import CommunityPhotoService
from app.services.storage import StorageService

# No prefix — full paths declared per-route so consumer / public / admin / me
# routes sit in their natural namespaces (mirrors masjid_submissions.router).
# Path templates carry the static `/community-photos` segment so they never
# collide with masjids.router's `/masjids/{masjid_id}` routes.
router = APIRouter(tags=["community-photos"])

# Coarse per-IP guard layered on top of the deterministic per-user/per-masjid
# DB caps inside the service.
_upload_limiter = make_rate_limiter(
    limit=30, window_s=3600, key_prefix="community_photo_upload"
)


# ── Consumer upload ────────────────────────────────────────────────────────────


@router.post(
    "/masjids/{masjid_id}/community-photos",
    response_model=CommunityPhotoSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a community photo for a masjid (authenticated, moderated)",
)
async def submit_community_photo(
    masjid_id: uuid.UUID,
    file: UploadFile = File(..., description="Image (JPEG, PNG, WebP), max 5 MB"),
    user: CurrentUser = Depends(get_current_user),
    _rl: None = Depends(_upload_limiter),
    service: CommunityPhotoService = Depends(get_community_photo_service),
    storage: StorageService = Depends(get_storage_service),
) -> CommunityPhotoSubmissionResponse:
    return await service.submit(masjid_id, file, user, storage)


# ── Public listing ─────────────────────────────────────────────────────────────


@router.get(
    "/masjids/{masjid_id}/community-photos",
    response_model=CommunityPhotoPublicListResponse,
    summary="List approved community photos for a masjid (public)",
)
async def list_community_photos(
    masjid_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: CommunityPhotoService = Depends(get_community_photo_service),
) -> CommunityPhotoPublicListResponse:
    return await service.list_public(masjid_id, page=page, page_size=page_size)


# ── Submitter ──────────────────────────────────────────────────────────────────


@router.get(
    "/me/photo-submissions",
    response_model=list[CommunityPhotoSubmissionResponse],
    summary="List my community photo submissions with status + timestamps",
)
async def list_my_photo_submissions(
    user: CurrentUser = Depends(get_current_user),
    service: CommunityPhotoService = Depends(get_community_photo_service),
) -> list[CommunityPhotoSubmissionResponse]:
    return await service.list_mine(user)


# ── Moderation (masjid admin + platform admin) ──────────────────────────────────


@router.get(
    "/admin/masjids/{masjid_id}/community-photos",
    response_model=CommunityPhotoModerationListResponse,
    summary="Moderation queue of community photos for a masjid (masjid_admin)",
)
async def list_community_photos_for_moderation(
    masjid_id: uuid.UUID,
    photo_status: str | None = Query(default="pending", alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(require_masjid_admin),
    service: CommunityPhotoService = Depends(get_community_photo_service),
) -> CommunityPhotoModerationListResponse:
    return await service.list_for_moderation(
        masjid_id,
        user,
        status_filter=photo_status,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/admin/community-photos/{photo_id}/approve",
    response_model=CommunityPhotoModerationResponse,
    summary="Approve a community photo (masjid_admin / platform_admin)",
)
async def approve_community_photo(
    photo_id: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: CommunityPhotoService = Depends(get_community_photo_service),
) -> CommunityPhotoModerationResponse:
    return await service.approve(photo_id, user)


@router.post(
    "/admin/community-photos/{photo_id}/reject",
    response_model=CommunityPhotoModerationResponse,
    summary="Reject a community photo (masjid_admin / platform_admin)",
)
async def reject_community_photo(
    photo_id: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: CommunityPhotoService = Depends(get_community_photo_service),
) -> CommunityPhotoModerationResponse:
    return await service.reject(photo_id, user)
