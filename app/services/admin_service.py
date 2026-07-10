"""Platform-admin dashboard orchestration.

Owns the cross-repository composition behind the admin router's stats/analytics
reads and the audited platform-wide push, so the router stays HTTP-only (no
AsyncSession, no repositories, no inline transaction control) — CODEBASE_AUDIT
#14 / #15.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.repositories.announcement_repository import AnnouncementRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.masjid_campaign_repository import MasjidCampaignRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.schemas.admin import (
    AdminStatsResponse,
    AuditLogEntry,
    AuditLogListResponse,
    UserGrowthPoint,
    UserGrowthResponse,
)
from app.schemas.push import BroadcastPushResponse
from app.services.masjid_service import MasjidService
from app.services.push_service import PushService


class AdminService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self.masjid_service = MasjidService(db)
        self.announcement_repo = AnnouncementRepository(db)
        self.profile_repo = UserProfileRepository(db)
        self.campaign_repo = MasjidCampaignRepository(db)
        self.audit_repo = AuditLogRepository(db)

    async def get_stats(self) -> AdminStatsResponse:
        stats = await self.masjid_service.get_stats()
        total_ann, published_ann = await self.announcement_repo.get_counts()
        total_users = await self.profile_repo.count_non_deleted()
        active_campaigns = await self.campaign_repo.get_active_count()
        return AdminStatsResponse(
            **stats,
            total_announcements=total_ann,
            published_announcements=published_ann,
            total_users=total_users,
            active_campaigns=active_campaigns,
        )

    async def get_audit_log(self, page: int, page_size: int) -> AuditLogListResponse:
        rows, total = await self.audit_repo.get_paginated(
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

    async def get_user_growth(self, period: str) -> UserGrowthResponse:
        data = await self.profile_repo.get_growth(period)
        return UserGrowthResponse(
            data=[UserGrowthPoint(period=p, count=c) for p, c in data],
            period=period,
        )

    async def broadcast_push(
        self, title: str, body: str, data: dict, user: CurrentUser
    ) -> BroadcastPushResponse:
        """Fan out a platform-wide push and audit the high-impact action, owning
        the commit here rather than in the HTTP layer (#15)."""
        count = await PushService(self._db).broadcast_platform_push(title, body, data)
        await self.audit_repo.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="broadcast_platform_push",
            target_entity="platform_push",
            details={"title": title, "devices_notified": count},
        )
        await self.audit_repo.commit()
        return BroadcastPushResponse(devices_notified=count)
