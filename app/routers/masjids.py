import uuid

from fastapi import APIRouter, Depends, Query, status

from app.core.security import CurrentUser
from app.dependencies.auth import require_masjid_admin, require_platform_admin
from app.dependencies.masjid import get_masjid_service
from app.schemas.masjid import (
    MasjidAdminListResponse,
    MasjidCreate,
    MasjidNearbyResult,
    MasjidResponse,
    MasjidSummary,
    MasjidUpdate,
    SuspendRequest,
)
from app.services.masjid_service import MasjidService

router = APIRouter(prefix="/masjids", tags=["masjids"])


# NOTE: /nearby and /search must be declared BEFORE /{masjid_id} — otherwise
# FastAPI tries to parse the literal string "nearby" as a UUID and returns 422.


@router.post(
    "",
    response_model=MasjidResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a masjid account (platform_admin + aal2)",
)
async def create_masjid(
    body: MasjidCreate,
    _user: CurrentUser = Depends(require_platform_admin),
    service: MasjidService = Depends(get_masjid_service),
) -> MasjidResponse:
    return await service.create(body)


@router.get(
    "/nearby",
    response_model=list[MasjidNearbyResult],
    summary="Find masjids within radius — public",
)
async def get_nearby(
    lat: float = Query(..., ge=-90.0, le=90.0, description="Latitude"),
    lng: float = Query(..., ge=-180.0, le=180.0, description="Longitude"),
    radius_m: float = Query(5000.0, ge=100.0, le=50_000.0, description="Radius in metres"),
    service: MasjidService = Depends(get_masjid_service),
) -> list[MasjidNearbyResult]:
    return await service.get_nearby(lat=lat, lng=lng, radius_m=radius_m)


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
    "",
    response_model=MasjidAdminListResponse,
    summary="List all masjids with filters (platform_admin + aal2)",
)
async def list_masjids(
    status_filter: str | None = Query(default=None, alias="status"),
    admin_region: str | None = Query(default=None),
    verified: bool | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _user: CurrentUser = Depends(require_platform_admin),
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
    _user: CurrentUser = Depends(require_platform_admin),
    service: MasjidService = Depends(get_masjid_service),
) -> MasjidResponse:
    return await service.verify(masjid_id)


@router.post(
    "/{masjid_id}/suspend",
    response_model=MasjidResponse,
    summary="Suspend masjid with reason (platform_admin + aal2)",
)
async def suspend_masjid(
    masjid_id: uuid.UUID,
    body: SuspendRequest,
    _user: CurrentUser = Depends(require_platform_admin),
    service: MasjidService = Depends(get_masjid_service),
) -> MasjidResponse:
    return await service.suspend(masjid_id, body.reason)
