import uuid

from fastapi import APIRouter, Depends

from app.core.security import CurrentUser
from app.dependencies.auth import require_masjid_admin
from app.dependencies.co_admin_invite import get_co_admin_invite_service
from app.schemas.co_admin_invite import (
    CoAdminInviteCreate,
    CoAdminInviteListResponse,
    CoAdminInviteResponse,
)
from app.services.co_admin_invite_service import CoAdminInviteService

router = APIRouter(prefix="/masjids", tags=["co-admins"])


@router.post(
    "/{masjid_id}/co-admins/invite",
    response_model=CoAdminInviteResponse,
    status_code=201,
    summary="Invite a co-admin to help manage this masjid",
)
async def invite_co_admin(
    masjid_id: uuid.UUID,
    body: CoAdminInviteCreate,
    user: CurrentUser = Depends(require_masjid_admin),
    service: CoAdminInviteService = Depends(get_co_admin_invite_service),
) -> CoAdminInviteResponse:
    return await service.invite(masjid_id, body, user)


@router.get(
    "/{masjid_id}/co-admins",
    response_model=CoAdminInviteListResponse,
    summary="List co-admin invites for this masjid",
)
async def list_co_admins(
    masjid_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    user: CurrentUser = Depends(require_masjid_admin),
    service: CoAdminInviteService = Depends(get_co_admin_invite_service),
) -> CoAdminInviteListResponse:
    return await service.list_invites(masjid_id, page, page_size, user)


@router.post(
    "/{masjid_id}/co-admins/{invite_id}/resend",
    response_model=CoAdminInviteResponse,
    summary="Resend a pending co-admin invite (max 3 times, 30 min cooldown)",
)
async def resend_invite(
    masjid_id: uuid.UUID,
    invite_id: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: CoAdminInviteService = Depends(get_co_admin_invite_service),
) -> CoAdminInviteResponse:
    return await service.resend(masjid_id, invite_id, user)


@router.delete(
    "/{masjid_id}/co-admins/{uid}",
    status_code=204,
    summary="Revoke a co-admin's access (uid = GoTrue user_id)",
)
async def revoke_co_admin(
    masjid_id: uuid.UUID,
    uid: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: CoAdminInviteService = Depends(get_co_admin_invite_service),
) -> None:
    await service.revoke(masjid_id, uid, user)
