import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser, decode_gotrue_sub
from app.models.enums import AdminRole
from app.models.masjid_co_admin_invite import MasjidCoAdminInvite
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.co_admin_invite_repository import CoAdminInviteRepository
from app.schemas.auth import TokenResponse
from app.schemas.co_admin_invite import (
    CoAdminAcceptRequest,
    CoAdminDeclineRequest,
    CoAdminInviteCreate,
    CoAdminInviteListResponse,
    CoAdminInviteResponse,
)
from app.services.gotrue_client import gotrue

INVITE_TTL_HOURS = 48
RESEND_LIMIT = 3
RESEND_COOLDOWN_MINUTES = 30


class CoAdminInviteService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = CoAdminInviteRepository(db)
        self.audit = AuditLogRepository(db)

    async def invite(
        self,
        masjid_id: uuid.UUID,
        data: CoAdminInviteCreate,
        user: CurrentUser,
    ) -> CoAdminInviteResponse:
        _check_scope(user, masjid_id)
        existing = await self.repo.get_pending_by_email_masjid(data.email, masjid_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A pending invite already exists for this email",
            )
        gt_data = await gotrue.create_admin_user(
            email=data.email,
            role=AdminRole.MASJID_ADMIN,
            masjid_id=masjid_id,
            send_invite=True,
        )
        invite = MasjidCoAdminInvite(
            masjid_id=masjid_id,
            invited_email=data.email,
            invited_by_id=user.user_id,
            invited_by_email=user.email,
            gotrue_user_id=uuid.UUID(gt_data["id"]),
            status="Pending",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=INVITE_TTL_HOURS),
        )
        await self.repo.add(invite)
        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="co_admin_invite",
            target_entity="masjid_co_admin_invite",
            target_id=invite.invite_id,
            details={"invited_email": data.email, "masjid_id": str(masjid_id)},
        )
        await self.repo.commit()
        return _to_response(invite)

    async def list_invites(
        self,
        masjid_id: uuid.UUID,
        page: int,
        page_size: int,
        user: CurrentUser,
    ) -> CoAdminInviteListResponse:
        _check_scope(user, masjid_id)
        rows, total = await self.repo.list_by_masjid(
            masjid_id, offset=(page - 1) * page_size, limit=page_size
        )
        return CoAdminInviteListResponse(
            items=[_to_response(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def resend(
        self,
        masjid_id: uuid.UUID,
        invite_id: uuid.UUID,
        user: CurrentUser,
    ) -> CoAdminInviteResponse:
        _check_scope(user, masjid_id)
        invite = await self.repo.get_pending_by_id_masjid(invite_id, masjid_id)
        if not invite:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pending invite not found",
            )
        now = datetime.now(timezone.utc)
        if invite.expires_at < now:
            invite.status = "Expired"
            await self.repo.commit()
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Invite has expired — create a new one",
            )
        if invite.resend_count >= RESEND_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Resend limit reached for this invite",
            )
        if invite.last_resent_at and (now - invite.last_resent_at) < timedelta(
            minutes=RESEND_COOLDOWN_MINUTES
        ):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Please wait before resending",
            )
        await gotrue.resend_invite_email(invite.invited_email)
        invite.resend_count += 1
        invite.last_resent_at = now
        invite.expires_at = now + timedelta(hours=INVITE_TTL_HOURS)
        await self.repo.commit()
        return _to_response(invite)

    async def accept(self, data: CoAdminAcceptRequest) -> TokenResponse:
        gotrue_user_id, _ = decode_gotrue_sub(data.token)
        invite = await self.repo.get_pending_by_gotrue_user(gotrue_user_id)
        if not invite:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invite not found or already actioned",
            )
        if invite.expires_at < datetime.now(timezone.utc):
            invite.status = "Expired"
            await self.repo.commit()
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Invite has expired",
            )
        await gotrue.update_user_password(data.token, data.password)
        invite.status = "Accepted"
        await self.repo.commit()
        token_data = await gotrue.login_with_password(
            invite.invited_email, data.password
        )
        return TokenResponse(
            access_token=token_data["access_token"],
            token_type=token_data.get("token_type", "bearer"),
            expires_in=token_data["expires_in"],
            refresh_token=token_data["refresh_token"],
        )

    async def decline(self, data: CoAdminDeclineRequest) -> None:
        gotrue_user_id, _ = decode_gotrue_sub(data.token)
        invite = await self.repo.get_pending_by_gotrue_user(gotrue_user_id)
        if not invite:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invite not found or already actioned",
            )
        await gotrue.delete_user(gotrue_user_id)
        invite.status = "Declined"
        await self.repo.commit()

    async def revoke(
        self,
        masjid_id: uuid.UUID,
        uid: uuid.UUID,
        user: CurrentUser,
    ) -> None:
        _check_scope(user, masjid_id)
        invite = await self.repo.get_active_by_gotrue_user_masjid(uid, masjid_id)
        if not invite:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active co-admin not found for this masjid",
            )
        await gotrue.delete_user(uid)
        invite.status = "Revoked"
        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="co_admin_revoke",
            target_entity="masjid_co_admin_invite",
            target_id=invite.invite_id,
            details={"revoked_gotrue_user_id": str(uid), "masjid_id": str(masjid_id)},
        )
        await self.repo.commit()


def _check_scope(user: CurrentUser, masjid_id: uuid.UUID) -> None:
    if user.is_platform_admin:
        return
    if user.masjid_id != masjid_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for this masjid",
        )


def _to_response(invite: MasjidCoAdminInvite) -> CoAdminInviteResponse:
    return CoAdminInviteResponse(
        invite_id=invite.invite_id,
        masjid_id=invite.masjid_id,
        invited_email=invite.invited_email,
        invited_by_email=invite.invited_by_email,
        gotrue_user_id=invite.gotrue_user_id,
        status=invite.status,
        expires_at=invite.expires_at,
        resend_count=invite.resend_count,
        created_at=invite.created_at,
        updated_at=invite.updated_at,
    )
