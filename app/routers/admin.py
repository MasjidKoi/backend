"""
Admin router — platform_admin only endpoints.

GET /admin/stats       — live masjid counters for the dashboard
GET /admin/audit-log   — paginated history of every admin write action
"""

from fastapi import APIRouter, Depends, Query

from app.core.security import CurrentUser
from app.dependencies.auth import require_platform_admin
from app.dependencies.masjid import get_masjid_service
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.admin import AdminStatsResponse, AuditLogEntry, AuditLogListResponse
from app.services.masjid_service import MasjidService
from app.db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/stats",
    response_model=AdminStatsResponse,
    summary="Live masjid counters (platform_admin + aal2)",
)
async def get_stats(
    _user: CurrentUser = Depends(require_platform_admin),
    service: MasjidService = Depends(get_masjid_service),
) -> AdminStatsResponse:
    stats = await service.get_stats()
    return AdminStatsResponse(**stats)


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
