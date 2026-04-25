"""
Admin router — platform_admin only endpoints.
"""

import uuid

import httpx
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.core.security import CurrentUser
from app.db.session import get_db
from app.dependencies.admin_user import get_admin_user_service
from app.dependencies.announcement import get_announcement_service
from app.dependencies.auth import require_platform_admin
from app.dependencies.masjid import get_masjid_service
from app.dependencies.platform_settings import get_platform_settings_service
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.masjid_campaign_repository import MasjidCampaignRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.schemas.admin import (
    AdminStatsResponse,
    AppUserListResponse,
    AppUserResponse,
    AuditLogEntry,
    AuditLogListResponse,
    SuspendRequest,
    UserGrowthPoint,
    UserGrowthResponse,
)
from app.schemas.announcement import AnnouncementPlatformListResponse
from app.schemas.platform_settings import (
    PlatformSettingsResponse,
    PlatformSettingsUpdate,
)
from app.services.admin_user_service import AdminUserService
from app.services.announcement_service import AnnouncementService
from app.services.masjid_service import MasjidService
from app.services.platform_settings_service import PlatformSettingsService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/stats",
    response_model=AdminStatsResponse,
    summary="Live platform counters (platform_admin)",
)
async def get_stats(
    _user: CurrentUser = Depends(require_platform_admin),
    service: MasjidService = Depends(get_masjid_service),
    ann_service: AnnouncementService = Depends(get_announcement_service),
    db: AsyncSession = Depends(get_db),
) -> AdminStatsResponse:
    stats = await service.get_stats()
    total_ann, published_ann = await ann_service.repo.get_counts()
    profile_repo = UserProfileRepository(db)
    campaign_repo = MasjidCampaignRepository(db)
    total_users = await profile_repo.count_non_deleted()
    active_campaigns = await campaign_repo.get_active_count()
    return AdminStatsResponse(
        **stats,
        total_announcements=total_ann,
        published_announcements=published_ann,
        total_users=total_users,
        active_campaigns=active_campaigns,
    )


@router.get(
    "/audit-log",
    response_model=AuditLogListResponse,
    summary="Paginated admin action log (platform_admin)",
)
async def get_audit_log(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _user: CurrentUser = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> AuditLogListResponse:
    repo = AuditLogRepository(db)
    rows, total = await repo.get_paginated(
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return AuditLogListResponse(
        items=[
            AuditLogEntry(
                log_id=r.log_id,
                admin_id=r.admin_id,
                admin_email=r.admin_email,
                admin_role=r.admin_role,
                action=r.action,
                target_entity=r.target_entity,
                target_id=r.target_id,
                ip_address=r.ip_address,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


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
    summary="List all admin users from GoTrue (platform_admin)",
)
async def list_admin_users(
    _user: CurrentUser = Depends(require_platform_admin),
) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(
            f"{app_settings.gotrue_base_url}/admin/users",
            headers={
                "Authorization": f"Bearer {app_settings.GOTRUE_SERVICE_ROLE_KEY}",
                "apikey": app_settings.GOTRUE_SERVICE_ROLE_KEY,
            },
        )
    if not resp.is_success:
        return {"users": []}
    data = resp.json()
    users = [
        {
            "id": u["id"],
            "email": u.get("email"),
            "role": u.get("app_metadata", {}).get("role"),
            "masjid_id": u.get("app_metadata", {}).get("masjid_id"),
            "created_at": u.get("created_at"),
            "confirmed_at": u.get("email_confirmed_at"),
            "invited_at": u.get("invited_at"),
        }
        for u in data.get("users", [])
    ]
    return {"users": users, "total": len(users)}


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
    db: AsyncSession = Depends(get_db),
) -> UserGrowthResponse:
    repo = UserProfileRepository(db)
    data = await repo.get_growth(period)
    return UserGrowthResponse(
        data=[UserGrowthPoint(period=p, count=c) for p, c in data],
        period=period,
    )


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
