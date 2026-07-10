"""
Admin router — platform_admin only endpoints.
"""

import uuid

from fastapi import APIRouter, Depends, Query, status

from app.core.security import CurrentUser
from app.dependencies.admin import get_admin_service
from app.dependencies.admin_user import get_admin_user_service
from app.dependencies.announcement import get_announcement_service
from app.dependencies.auth import require_platform_admin
from app.dependencies.platform_settings import get_platform_settings_service
from app.schemas.admin import (
    AdminStatsResponse,
    AdminUserListResponse,
    AppUserListResponse,
    AppUserResponse,
    AuditLogListResponse,
    SuspendRequest,
    UserGrowthResponse,
)
from app.schemas.announcement import AnnouncementPlatformListResponse
from app.schemas.platform_settings import (
    PlatformSettingsResponse,
    PlatformSettingsUpdate,
)
from app.schemas.push import BroadcastPushRequest, BroadcastPushResponse
from app.services.admin_service import AdminService
from app.services.admin_user_service import AdminUserService
from app.services.announcement_service import AnnouncementService
from app.services.platform_settings_service import PlatformSettingsService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/stats",
    response_model=AdminStatsResponse,
    summary="Live platform counters (platform_admin)",
)
async def get_stats(
    _user: CurrentUser = Depends(require_platform_admin),
    service: AdminService = Depends(get_admin_service),
) -> AdminStatsResponse:
    return await service.get_stats()


@router.get(
    "/audit-log",
    response_model=AuditLogListResponse,
    summary="Paginated admin action log (platform_admin)",
)
async def get_audit_log(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _user: CurrentUser = Depends(require_platform_admin),
    service: AdminService = Depends(get_admin_service),
) -> AuditLogListResponse:
    return await service.get_audit_log(page, page_size)


@router.get(
    "/announcements",
    response_model=AnnouncementPlatformListResponse,
    summary="List all announcements across all masjids (platform_admin)",
)
async def list_all_announcements(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    masjid_id: uuid.UUID | None = Query(default=None),
    _user: CurrentUser = Depends(require_platform_admin),
    service: AnnouncementService = Depends(get_announcement_service),
) -> AnnouncementPlatformListResponse:
    return await service.list_platform(page, page_size, masjid_id)


@router.get(
    "/users",
    response_model=AdminUserListResponse,
    summary="List all admin users from GoTrue (platform_admin)",
)
async def list_admin_users(
    _user: CurrentUser = Depends(require_platform_admin),
    service: AdminUserService = Depends(get_admin_user_service),
) -> AdminUserListResponse:
    return await service.list_admin_users()


# ── App-User Management ───────────────────────────────────────────────────────


@router.get(
    "/app-users",
    response_model=AppUserListResponse,
    summary="List mobile app users (platform_admin)",
)
async def list_app_users(
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _user: CurrentUser = Depends(require_platform_admin),
    service: AdminUserService = Depends(get_admin_user_service),
) -> AppUserListResponse:
    return await service.list_app_users(search, page, page_size)


@router.post(
    "/app-users/{user_id}/suspend",
    response_model=AppUserResponse,
    summary="Suspend a mobile app user (platform_admin)",
)
async def suspend_user(
    user_id: uuid.UUID,
    body: SuspendRequest,
    acting_user: CurrentUser = Depends(require_platform_admin),
    service: AdminUserService = Depends(get_admin_user_service),
) -> AppUserResponse:
    return await service.suspend(user_id, body.reason, acting_user)


@router.post(
    "/app-users/{user_id}/unsuspend",
    response_model=AppUserResponse,
    summary="Unsuspend a mobile app user (platform_admin)",
)
async def unsuspend_user(
    user_id: uuid.UUID,
    acting_user: CurrentUser = Depends(require_platform_admin),
    service: AdminUserService = Depends(get_admin_user_service),
) -> AppUserResponse:
    return await service.unsuspend(user_id, acting_user)


@router.delete(
    "/app-users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a mobile app user (platform_admin)",
)
async def delete_app_user(
    user_id: uuid.UUID,
    acting_user: CurrentUser = Depends(require_platform_admin),
    service: AdminUserService = Depends(get_admin_user_service),
) -> None:
    await service.delete(user_id, acting_user)


# ── Analytics ─────────────────────────────────────────────────────────────────


@router.get(
    "/analytics/user-growth",
    response_model=UserGrowthResponse,
    summary="User registration growth over time (platform_admin)",
)
async def user_growth(
    period: str = Query(default="daily", pattern="^(daily|weekly|monthly)$"),
    _user: CurrentUser = Depends(require_platform_admin),
    service: AdminService = Depends(get_admin_service),
) -> UserGrowthResponse:
    return await service.get_user_growth(period)


# ── Platform Settings ─────────────────────────────────────────────────────────


@router.get(
    "/settings",
    response_model=PlatformSettingsResponse,
    summary="Get platform-wide settings (platform_admin)",
)
async def get_settings(
    _user: CurrentUser = Depends(require_platform_admin),
    service: PlatformSettingsService = Depends(get_platform_settings_service),
) -> PlatformSettingsResponse:
    settings = await service.get()
    return PlatformSettingsResponse.model_validate(settings)


@router.patch(
    "/settings",
    response_model=PlatformSettingsResponse,
    summary="Update platform-wide settings (platform_admin)",
)
async def update_settings(
    body: PlatformSettingsUpdate,
    user: CurrentUser = Depends(require_platform_admin),
    service: PlatformSettingsService = Depends(get_platform_settings_service),
) -> PlatformSettingsResponse:
    settings = await service.update(body, user)
    return PlatformSettingsResponse.model_validate(settings)


# ── Platform-wide push ──────────────────────────────────────────────────────


@router.post(
    "/broadcast-push",
    response_model=BroadcastPushResponse,
    summary="Broadcast a push to every device (platform_admin)",
)
async def broadcast_push(
    body: BroadcastPushRequest,
    user: CurrentUser = Depends(require_platform_admin),
    service: AdminService = Depends(get_admin_service),
) -> BroadcastPushResponse:
    # PRD 03 PLATFORM_PUSH — Eid / Ramadan-start / urgent notices. The service
    # owns the best-effort fan-out, the audit write, and the commit.
    return await service.broadcast_push(body.title, body.body, body.data, user)
