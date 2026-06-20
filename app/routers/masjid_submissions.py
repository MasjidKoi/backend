import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile, status

from app.core.rate_limit import make_rate_limiter
from app.core.security import CurrentUser
from app.dependencies.auth import get_current_user, require_platform_admin
from app.dependencies.masjid_submission import get_masjid_submission_service
from app.dependencies.storage import get_storage_service
from app.schemas.masjid_submission import (
    MasjidSubmissionAdminResponse,
    MasjidSubmissionApprove,
    MasjidSubmissionCreate,
    MasjidSubmissionListResponse,
    MasjidSubmissionResponse,
    SubmissionPhotoUploadResponse,
)
from app.services.masjid_submission_service import MasjidSubmissionService
from app.services.storage import StorageService

# No prefix — full paths declared per-route so they sit in distinct namespaces
# (/masjids/submissions, /me/submissions, /admin/submissions). This router MUST be
# included BEFORE masjids.router in main.py so the static /masjids/submissions path
# is matched before /masjids/{masjid_id} (which would otherwise 422 on the UUID).
router = APIRouter(tags=["submissions"])

_submission_limiter = make_rate_limiter(
    limit=10, window_s=3600, key_prefix="masjid_submission"
)
_photo_limiter = make_rate_limiter(
    limit=30, window_s=3600, key_prefix="masjid_submission_photo"
)


# ── Consumer ─────────────────────────────────────────────────────────────────


@router.post(
    "/masjids/submissions",
    response_model=MasjidSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a missing masjid for review (authenticated, rate limited)",
)
async def create_submission(
    body: MasjidSubmissionCreate,
    user: CurrentUser = Depends(get_current_user),
    _rl: None = Depends(_submission_limiter),
    service: MasjidSubmissionService = Depends(get_masjid_submission_service),
) -> MasjidSubmissionResponse:
    return await service.create(body, user)


@router.post(
    "/masjids/submissions/photo",
    response_model=SubmissionPhotoUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a submission photo, returns a photo_key (authenticated)",
)
async def upload_submission_photo(
    file: UploadFile = File(..., description="Image (JPEG, PNG, WebP), max 5 MB"),
    user: CurrentUser = Depends(get_current_user),
    _rl: None = Depends(_photo_limiter),
    service: MasjidSubmissionService = Depends(get_masjid_submission_service),
    storage: StorageService = Depends(get_storage_service),
) -> SubmissionPhotoUploadResponse:
    return await service.upload_photo(file, user, storage)


@router.get(
    "/me/submissions",
    response_model=list[MasjidSubmissionResponse],
    summary="List my masjid submissions with status + approved masjid id",
)
async def list_my_submissions(
    user: CurrentUser = Depends(get_current_user),
    service: MasjidSubmissionService = Depends(get_masjid_submission_service),
) -> list[MasjidSubmissionResponse]:
    return await service.list_mine(user)


# ── Admin ────────────────────────────────────────────────────────────────────


@router.get(
    "/admin/submissions",
    response_model=MasjidSubmissionListResponse,
    summary="List masjid submissions (platform_admin)",
)
async def list_submissions(
    submission_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(require_platform_admin),
    service: MasjidSubmissionService = Depends(get_masjid_submission_service),
) -> MasjidSubmissionListResponse:
    return await service.list_for_admin(
        status_filter=submission_status,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/admin/submissions/{submission_id}/approve",
    response_model=MasjidSubmissionAdminResponse,
    summary="Approve a submission — creates the live masjid (platform_admin)",
)
async def approve_submission(
    submission_id: uuid.UUID,
    body: MasjidSubmissionApprove,
    user: CurrentUser = Depends(require_platform_admin),
    service: MasjidSubmissionService = Depends(get_masjid_submission_service),
) -> MasjidSubmissionAdminResponse:
    return await service.approve(submission_id, body, user)


@router.post(
    "/admin/submissions/{submission_id}/reject",
    response_model=MasjidSubmissionAdminResponse,
    summary="Reject a submission (platform_admin)",
)
async def reject_submission(
    submission_id: uuid.UUID,
    user: CurrentUser = Depends(require_platform_admin),
    service: MasjidSubmissionService = Depends(get_masjid_submission_service),
) -> MasjidSubmissionAdminResponse:
    return await service.reject(submission_id, user)
