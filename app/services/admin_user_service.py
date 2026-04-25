import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.models.user_profile import UserProfile
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.schemas.admin import AppUserListResponse, AppUserResponse
from app.services.gotrue_client import gotrue


def _to_response(profile: UserProfile) -> AppUserResponse:
    return AppUserResponse(
        user_id=profile.user_id,
        display_name=profile.display_name,
        madhab=profile.madhab,
        profile_photo_url=profile.profile_photo_url,
        is_suspended=profile.is_suspended,
        suspended_at=profile.suspended_at,
        suspension_reason=profile.suspension_reason,
        is_deleted=profile.is_deleted,
        created_at=profile.created_at,
    )


class AdminUserService:
    def __init__(self, db: AsyncSession) -> None:
        self.profile_repo = UserProfileRepository(db)
        self.audit_repo = AuditLogRepository(db)

    async def list_app_users(
        self, search: str | None, page: int, page_size: int
    ) -> AppUserListResponse:
        rows, total = await self.profile_repo.list_all(
            search, offset=(page - 1) * page_size, limit=page_size
        )
        return AppUserListResponse(
            items=[_to_response(p) for p in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def _get_profile_or_404(self, user_id: uuid.UUID) -> UserProfile:
        profile = await self.profile_repo.get_by_user_id(user_id)
        if not profile or profile.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )
        return profile

    async def suspend(
        self, user_id: uuid.UUID, reason: str, acting_user: CurrentUser
    ) -> AppUserResponse:
        profile = await self._get_profile_or_404(user_id)
        if profile.is_suspended:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User is already suspended"
            )
        profile.is_suspended = True
        profile.suspended_at = datetime.now(timezone.utc)
        profile.suspension_reason = reason
        await self.profile_repo.db.flush()
        await gotrue.ban_user(user_id)
        await self.profile_repo.commit()
        return _to_response(profile)

    async def unsuspend(
        self, user_id: uuid.UUID, acting_user: CurrentUser
    ) -> AppUserResponse:
        profile = await self._get_profile_or_404(user_id)
        if not profile.is_suspended:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User is not suspended"
            )
        profile.is_suspended = False
        profile.suspended_at = None
        profile.suspension_reason = None
        await self.profile_repo.db.flush()
        await gotrue.ban_user(user_id, duration="none")
        await self.profile_repo.commit()
        return _to_response(profile)

    async def delete(self, user_id: uuid.UUID, acting_user: CurrentUser) -> None:
        profile = await self._get_profile_or_404(user_id)
        await self.profile_repo.soft_delete(profile)
        await gotrue.delete_user(user_id)
        await self.profile_repo.commit()
