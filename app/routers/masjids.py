import io
import json
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.rate_limit import make_rate_limiter
from app.core.security import CurrentUser
from app.dependencies.auth import (
    get_current_user,
    require_masjid_admin,
    require_platform_admin,
)
from app.dependencies.masjid import get_masjid_service
from app.dependencies.masjid_photo import get_masjid_photo_service
from app.dependencies.masjid_report import get_masjid_report_service
from app.dependencies.masjid_review import get_masjid_review_service
from app.dependencies.storage import get_storage_service
from app.dependencies.user_masjid_follow import get_follow_service
from app.schemas.masjid import (
    BulkImportResponse,
    MasjidAdminListResponse,
    MasjidCreate,
    MasjidMergeRequest,
    MasjidNearbyResult,
    MasjidResponse,
    MasjidSummary,
    MasjidUpdate,
    PhotoReorderRequest,
    PhotoResponse,
    SuspendRequest,
)
from app.schemas.masjid_report import (
    MasjidReportAdminResponse,
    MasjidReportCreate,
    MasjidReportListResponse,
    MasjidReportResponse,
    MasjidReportUpdateStatus,
)
from app.schemas.masjid_review import (
    MasjidReviewCreate,
    MasjidReviewListResponse,
    MasjidReviewResponse,
)
from app.services.masjid_photo_service import MasjidPhotoService
from app.services.masjid_report_service import MasjidReportService
from app.services.masjid_review_service import MasjidReviewService
from app.services.masjid_service import MasjidService
from app.services.storage import StorageService
from app.services.user_masjid_follow_service import UserMasjidFollowService

router = APIRouter(prefix="/masjids", tags=["masjids"])

_nearby_limiter = make_rate_limiter(limit=30, window_s=60, key_prefix="nearby")
_report_limiter = make_rate_limiter(limit=5, window_s=60, key_prefix="report")

# NOTE: all static paths (/nearby, /search, /bulk-import, /export, /merge, /reports)
# must be declared BEFORE /{masjid_id} — otherwise FastAPI tries to parse the
# literal string as a UUID and returns 422.


@router.post(
    "",
    response_model=MasjidResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a masjid account (platform_admin + aal2)",
)
async def create_masjid(
    body: MasjidCreate,
    user: CurrentUser = Depends(require_platform_admin),
    service: MasjidService = Depends(get_masjid_service),
) -> MasjidResponse:
    return await service.create(body, user)


@router.get(
    "/nearby",
    response_model=list[MasjidNearbyResult],
    summary="Find masjids within radius — public",
)
async def get_nearby(
    lat: float = Query(..., ge=-90.0, le=90.0, description="Latitude"),
    lng: float = Query(..., ge=-180.0, le=180.0, description="Longitude"),
    radius_m: float = Query(
        5000.0, ge=100.0, le=50_000.0, description="Radius in metres"
    ),
    has_parking: bool | None = Query(default=None, description="Filter: has parking"),
    has_sisters_section: bool | None = Query(
        default=None, description="Filter: has sisters section"
    ),
    has_wheelchair_access: bool | None = Query(
        default=None, description="Filter: wheelchair accessible"
    ),
    has_wudu_area: bool | None = Query(
        default=None, description="Filter: has wudu area"
    ),
    has_janazah: bool | None = Query(
        default=None, description="Filter: has Janazah facility"
    ),
    has_school: bool | None = Query(
        default=None, description="Filter: has Islamic school"
    ),
    _rl: None = Depends(_nearby_limiter),
    service: MasjidService = Depends(get_masjid_service),
) -> list[MasjidNearbyResult]:
    return await service.get_nearby(
        lat=lat,
        lng=lng,
        radius_m=radius_m,
        has_parking=has_parking,
        has_sisters_section=has_sisters_section,
        has_wheelchair_access=has_wheelchair_access,
        has_wudu_area=has_wudu_area,
        has_janazah=has_janazah,
        has_school=has_school,
    )


@router.get(
    "/search",
    response_model=list[MasjidSummary],
    summary="Search masjids by name or area — public",
)
async def search_masjids(
    q: str = Query(..., min_length=2, description="Search query (min 2 chars)"),
    service: MasjidService = Depends(get_masjid_service),
) -> list[MasjidSummary]:
    return await service.search(q)


@router.get(
    "/bulk-import/fields",
    summary="Return accepted field names for bulk import — public",
)
async def bulk_import_fields() -> dict:
    return {
        "required": ["name", "address", "admin_region", "lat", "lng"],
        "optional": ["timezone", "description", "donations_enabled"],
    }


@router.post(
    "/bulk-import",
    response_model=BulkImportResponse,
    status_code=status.HTTP_200_OK,
    summary="Bulk import masjids from CSV or XLSX (platform_admin)",
)
async def bulk_import_masjids(
    file: UploadFile = File(..., description="CSV or XLSX file, max 10 MB"),
    field_map: str | None = Form(
        default=None,
        description='Optional JSON mapping of source column names to canonical field names, e.g. {"Masjid Name":"name"}',
    ),
    user: CurrentUser = Depends(require_platform_admin),
    service: MasjidService = Depends(get_masjid_service),
    storage: StorageService = Depends(get_storage_service),
) -> BulkImportResponse:
    parsed_field_map: dict[str, str] | None = None
    if field_map:
        parsed_field_map = json.loads(field_map)
    return await service.bulk_import(
        file=file, user=user, storage=storage, field_map=parsed_field_map
    )


@router.get(
    "/export",
    response_class=StreamingResponse,
    summary="Export masjid directory as CSV or PDF (platform_admin)",
    responses={
        200: {
            "description": "File download",
            "content": {"text/csv": {}, "application/pdf": {}},
        }
    },
)
async def export_masjids(
    format: str = Query(default="csv", pattern="^(csv|pdf)$", description="csv or pdf"),
    status_filter: str | None = Query(default=None, alias="status"),
    admin_region: str | None = Query(default=None),
    verified: bool | None = Query(default=None),
    user: CurrentUser = Depends(require_platform_admin),
    service: MasjidService = Depends(get_masjid_service),
) -> StreamingResponse:
    result = await service.export(
        format=format,
        status_filter=status_filter,
        admin_region=admin_region,
        verified=verified,
    )
    return StreamingResponse(
        io.BytesIO(result.data),
        media_type=result.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "Content-Length": str(len(result.data)),
        },
    )


@router.post(
    "/merge",
    response_model=MasjidResponse,
    status_code=status.HTTP_200_OK,
    summary="Merge duplicate masjid entries (platform_admin)",
)
async def merge_masjids(
    body: MasjidMergeRequest,
    user: CurrentUser = Depends(require_platform_admin),
    service: MasjidService = Depends(get_masjid_service),
) -> MasjidResponse:
    return await service.merge(body, user)


@router.get(
    "/reports",
    response_model=MasjidReportListResponse,
    summary="List masjid reports (platform_admin)",
)
async def list_reports(
    report_status: str | None = Query(default=None, alias="status"),
    masjid_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(require_platform_admin),
    service: MasjidReportService = Depends(get_masjid_report_service),
) -> MasjidReportListResponse:
    return await service.list_reports(
        status_filter=report_status,
        masjid_id=masjid_id,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/reports/{report_id}",
    response_model=MasjidReportAdminResponse,
    summary="Update report status (platform_admin)",
)
async def update_report_status(
    report_id: uuid.UUID,
    body: MasjidReportUpdateStatus,
    user: CurrentUser = Depends(require_platform_admin),
    service: MasjidReportService = Depends(get_masjid_report_service),
) -> MasjidReportAdminResponse:
    return await service.update_report_status(report_id, body.status, user)


@router.get(
    "",
    response_model=MasjidAdminListResponse,
    summary="List masjids with filters (public)",
)
async def list_masjids(
    status_filter: str | None = Query(default=None, alias="status"),
    admin_region: str | None = Query(default=None),
    verified: bool | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    service: MasjidService = Depends(get_masjid_service),
) -> MasjidAdminListResponse:
    return await service.list_for_admin(
        status_filter=status_filter,
        admin_region=admin_region,
        verified=verified,
        q=q,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{masjid_id}",
    response_model=MasjidResponse,
    summary="Get full masjid profile — public",
)
async def get_masjid(
    masjid_id: uuid.UUID,
    service: MasjidService = Depends(get_masjid_service),
) -> MasjidResponse:
    return await service.get_by_id(masjid_id)


@router.patch(
    "/{masjid_id}",
    response_model=MasjidResponse,
    summary="Update masjid profile (masjid_admin scoped to own masjid)",
)
async def update_masjid(
    masjid_id: uuid.UUID,
    body: MasjidUpdate,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidService = Depends(get_masjid_service),
) -> MasjidResponse:
    return await service.update(masjid_id, body, user)


@router.post(
    "/{masjid_id}/verify",
    response_model=MasjidResponse,
    summary="Grant verified badge (platform_admin + aal2)",
)
async def verify_masjid(
    masjid_id: uuid.UUID,
    user: CurrentUser = Depends(require_platform_admin),
    service: MasjidService = Depends(get_masjid_service),
) -> MasjidResponse:
    return await service.verify(masjid_id, user)


@router.post(
    "/{masjid_id}/suspend",
    response_model=MasjidResponse,
    summary="Suspend masjid with reason (platform_admin + aal2)",
)
async def suspend_masjid(
    masjid_id: uuid.UUID,
    body: SuspendRequest,
    user: CurrentUser = Depends(require_platform_admin),
    service: MasjidService = Depends(get_masjid_service),
) -> MasjidResponse:
    return await service.suspend(masjid_id, body.reason, user)


@router.post(
    "/{masjid_id}/report",
    response_model=MasjidReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Report incorrect masjid information — public, rate limited",
)
async def report_masjid(
    masjid_id: uuid.UUID,
    body: MasjidReportCreate,
    _rl: None = Depends(_report_limiter),
    service: MasjidReportService = Depends(get_masjid_report_service),
) -> MasjidReportResponse:
    return await service.create_report(masjid_id, body)


@router.post(
    "/{masjid_id}/follow",
    status_code=status.HTTP_201_CREATED,
    summary="Follow a masjid (authenticated user)",
)
async def follow_masjid(
    masjid_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    service: UserMasjidFollowService = Depends(get_follow_service),
) -> JSONResponse:
    await service.follow(masjid_id, user)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"detail": "Masjid followed"},
    )


@router.delete(
    "/{masjid_id}/follow",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unfollow a masjid (authenticated user)",
)
async def unfollow_masjid(
    masjid_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    service: UserMasjidFollowService = Depends(get_follow_service),
) -> None:
    await service.unfollow(masjid_id, user)


@router.get(
    "/{masjid_id}/followers/count",
    summary="Get follower count for a masjid — public",
)
async def get_follower_count(
    masjid_id: uuid.UUID,
    service: UserMasjidFollowService = Depends(get_follow_service),
) -> dict:
    return await service.get_follower_count(masjid_id)


@router.post(
    "/{masjid_id}/reviews",
    response_model=MasjidReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a star rating + review for a masjid (authenticated)",
)
async def submit_review(
    masjid_id: uuid.UUID,
    body: MasjidReviewCreate,
    user: CurrentUser = Depends(get_current_user),
    service: MasjidReviewService = Depends(get_masjid_review_service),
) -> MasjidReviewResponse:
    return await service.submit_review(masjid_id, user, body)


@router.get(
    "/{masjid_id}/reviews",
    response_model=MasjidReviewListResponse,
    summary="List reviews for a masjid — public, paginated",
)
async def list_reviews(
    masjid_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: MasjidReviewService = Depends(get_masjid_review_service),
) -> MasjidReviewListResponse:
    return await service.list_reviews(masjid_id, page, page_size)


@router.delete(
    "/{masjid_id}/reviews/{review_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a review (masjid_admin or platform_admin)",
)
async def delete_review(
    masjid_id: uuid.UUID,
    review_id: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidReviewService = Depends(get_masjid_review_service),
) -> None:
    await service.delete_review(masjid_id, review_id, user)


@router.post(
    "/{masjid_id}/photos",
    response_model=PhotoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a photo for a masjid (masjid_admin)",
)
async def upload_photo(
    masjid_id: uuid.UUID,
    file: UploadFile = File(..., description="Image file (JPEG, PNG, WebP), max 5 MB"),
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidPhotoService = Depends(get_masjid_photo_service),
    storage: StorageService = Depends(get_storage_service),
) -> PhotoResponse:
    return await service.upload(masjid_id, file, user, storage)


@router.delete(
    "/{masjid_id}/photos/{photo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a masjid photo (masjid_admin)",
)
async def delete_photo(
    masjid_id: uuid.UUID,
    photo_id: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidPhotoService = Depends(get_masjid_photo_service),
    storage: StorageService = Depends(get_storage_service),
) -> None:
    await service.delete_photo(masjid_id, photo_id, user, storage)


@router.post(
    "/{masjid_id}/photos/{photo_id}/cover",
    response_model=list[PhotoResponse],
    summary="Set a photo as the cover image (masjid_admin)",
)
async def set_cover_photo(
    masjid_id: uuid.UUID,
    photo_id: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidPhotoService = Depends(get_masjid_photo_service),
) -> list[PhotoResponse]:
    return await service.set_cover(masjid_id, photo_id, user)


@router.put(
    "/{masjid_id}/photos/reorder",
    response_model=list[PhotoResponse],
    summary="Reorder masjid photos (masjid_admin)",
)
async def reorder_photos(
    masjid_id: uuid.UUID,
    body: PhotoReorderRequest,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidPhotoService = Depends(get_masjid_photo_service),
) -> list[PhotoResponse]:
    return await service.reorder(masjid_id, body.ordered_photo_ids, user)
