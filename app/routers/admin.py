"""
Admin router — platform_admin only endpoints.

GET /admin/stats       — live masjid counters for the dashboard
GET /admin/audit-log   — paginated history of every admin write action
"""

from fastapi import APIRouter, Depends, Query

import uuid

from app.core.security import CurrentUser
from app.dependencies.auth import require_platform_admin
from app.dependencies.masjid import get_masjid_service
from app.dependencies.announcement import get_announcement_service
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.admin import AdminStatsResponse, AuditLogEntry, AuditLogListResponse
from app.schemas.announcement import AnnouncementPlatformListResponse
from app.services.masjid_service import MasjidService
from app.services.announcement_service import AnnouncementService
from app.db.session import get_db
from app.core.config import settings
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/stats",
    response_model=AdminStatsResponse,
    summary="Live masjid counters (platform_admin + aal2)",
)
async def get_stats(
    _user: CurrentUser = Depends(require_platform_admin),
    service: MasjidService = Depends(get_masjid_service),
    ann_service: AnnouncementService = Depends(get_announcement_service),
) -> AdminStatsResponse:
    stats = await service.get_stats()
    total_ann, published_ann = await ann_service.repo.get_counts()
    return AdminStatsResponse(
        **stats,
        total_announcements=total_ann,
        published_announcements=published_ann,
    )


@router.get(
    "/audit-log",
    response_model=AuditLogListResponse,
    summary="Paginated admin action log (platform_admin + aal2)",
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
async def list_users(
    _user: CurrentUser = Depends(require_platform_admin),
) -> dict:
    """Fetches the GoTrue admin users list using the service_role key."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(
            f"{settings.gotrue_base_url}/admin/users",
            headers={
                "Authorization": f"Bearer {settings.GOTRUE_SERVICE_ROLE_KEY}",
                "apikey": settings.GOTRUE_SERVICE_ROLE_KEY,
            },
        )
    if not resp.is_success:
        return {"users": []}
    data = resp.json()
    # Return simplified user objects
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
