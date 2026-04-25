"""
Auth router — proxies login/refresh/logout/MFA to GoTrue.

FastAPI acts as the single entry point; clients never call GoTrue directly.
This gives us a place to add rate limiting, audit logging, and TOTP
enforcement without changing the JWT issuing logic.

Endpoint map:
  POST /auth/login          → GoTrue /token?grant_type=password
  POST /auth/refresh        → GoTrue /token?grant_type=refresh_token
  POST /auth/logout         → GoTrue /logout
  POST /auth/2fa/enroll     → GoTrue /factors  (platform_admin only)
  POST /auth/2fa/verify     → GoTrue /factors/{id}/verify
  POST /auth/password/reset → GoTrue /recover
  POST /auth/admin/invite   → GoTrue /admin/users  (platform_admin, creates masjid/madrasha admin)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.db.session import get_db
from app.dependencies.auth import get_current_user, require_platform_admin
from app.dependencies.co_admin_invite import get_co_admin_invite_service
from app.models.enums import AdminRole
from app.schemas.auth import (
    AdminInviteRequest,
    AdminInviteResponse,
    LoginRequest,
    PasswordResetRequest,
    RefreshRequest,
    TokenResponse,
    TOTPEnrollResponse,
    TOTPVerifyRequest,
)
from app.schemas.co_admin_invite import CoAdminAcceptRequest, CoAdminDeclineRequest
from app.services.co_admin_invite_service import CoAdminInviteService
from app.services.gotrue_client import gotrue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=True)


# ── Login ──────────────────────────────────────────────────────────────────────


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Admin login (email + password)",
    description=(
        "Returns a JWT with aal=aal1. Platform admins MUST then call "
        "POST /auth/2fa/verify to upgrade to aal2 before accessing "
        "protected admin endpoints."
    ),
)
async def login(body: LoginRequest) -> TokenResponse:
    data = await gotrue.login_with_password(body.email, body.password)
    return TokenResponse(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=data["expires_in"],
        refresh_token=data["refresh_token"],
    )


# ── Refresh ────────────────────────────────────────────────────────────────────


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
)
async def refresh(body: RefreshRequest) -> TokenResponse:
    data = await gotrue.refresh_token(body.refresh_token)
    return TokenResponse(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=data["expires_in"],
        refresh_token=data["refresh_token"],
    )


# ── Logout ─────────────────────────────────────────────────────────────────────


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout — revokes all refresh tokens",
)
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> None:
    await gotrue.logout(credentials.credentials)


# ── Set password (invite flow) ─────────────────────────────────────────────────


class UpdatePasswordRequest(BaseModel):
    password: str = Field(..., min_length=8, description="New password (min 8 chars)")


@router.put(
    "/user/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set password for an invited user",
    description=(
        "Called by the /invite/accept frontend page. "
        "The Authorization header must carry the invite access_token from the email link hash. "
        "Sets the user's password in GoTrue so they can log in normally."
    ),
)
async def update_password(
    body: UpdatePasswordRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> None:
    await gotrue.update_user_password(credentials.credentials, body.password)


# ── TOTP / 2FA ─────────────────────────────────────────────────────────────────


@router.post(
    "/2fa/enroll",
    response_model=TOTPEnrollResponse,
    summary="Enroll TOTP authenticator (platform_admin only)",
    description=(
        "Initiates TOTP factor enrollment. Returns a totp_uri and QR code PNG "
        "to display in the frontend. The factor is not active until verified "
        "with POST /auth/2fa/verify."
    ),
)
async def enroll_totp(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    user: CurrentUser = Depends(get_current_user),
) -> TOTPEnrollResponse:
    if user.role != AdminRole.PLATFORM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="TOTP enrollment is only available to platform admins",
        )
    data = await gotrue.enroll_totp(credentials.credentials)
    totp_data = data.get("totp", {})
    return TOTPEnrollResponse(
        factor_id=data["id"],
        totp_uri=totp_data.get("uri", ""),
        qr_code=totp_data.get("qr_code", ""),
    )


@router.post(
    "/2fa/verify",
    response_model=TokenResponse,
    summary="Verify TOTP code — upgrades session to aal2",
    description=(
        "Verifies a 6-digit TOTP code for the enrolled factor. "
        "On success, GoTrue issues a new JWT with aal=aal2. "
        "Platform admins MUST hold an aal2 token to access /admin/* endpoints."
    ),
)
async def verify_totp(
    body: TOTPVerifyRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> TokenResponse:
    data = await gotrue.verify_totp(credentials.credentials, body.factor_id, body.code)
    return TokenResponse(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=data["expires_in"],
        refresh_token=data["refresh_token"],
    )


# ── List enrolled 2FA factors ─────────────────────────────────────────────────


@router.get(
    "/2fa/factors",
    summary="List enrolled TOTP factors for the current user",
    description="Returns verified TOTP factors from DB. Used by frontend to determine "
    "whether to show /login/enroll (empty) or /login/2fa (has factors).",
)
async def list_factors(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from sqlalchemy import text as sql_text

    result = await db.execute(
        sql_text(
            "SELECT id, status, friendly_name "
            "FROM auth.mfa_factors "
            "WHERE user_id = :uid AND factor_type = 'totp' AND status = 'verified' "
            "ORDER BY created_at"
        ),
        {"uid": str(user.user_id)},
    )
    rows = result.mappings().all()
    return {
        "factors": [
            {
                "id": str(r["id"]),
                "status": r["status"],
                "friendly_name": r["friendly_name"],
            }
            for r in rows
        ]
    }


# ── Password reset ─────────────────────────────────────────────────────────────


@router.post(
    "/password/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Request password reset email",
)
async def request_password_reset(body: PasswordResetRequest) -> None:
    await gotrue.request_password_reset(body.email)


# ── Admin: invite new admin user ───────────────────────────────────────────────


@router.post(
    "/admin/invite",
    response_model=AdminInviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a new masjid/madrasha admin (platform_admin only)",
    description=(
        "Creates a GoTrue user with the given role in app_metadata and sends "
        "an invite email. The invitee sets their password on first login. "
        "Requires platform_admin with aal2."
    ),
)
async def invite_admin(
    body: AdminInviteRequest,
    _: CurrentUser = Depends(require_platform_admin),
) -> AdminInviteResponse:
    data = await gotrue.create_admin_user(
        email=str(body.email),
        role=body.role,
        masjid_id=body.masjid_id,
        madrasha_id=body.madrasha_id,
        send_invite=True,
    )
    return AdminInviteResponse(
        gotrue_user_id=data["id"],
        email=data["email"],
        role=body.role,
    )


# ── Co-admin invite accept / decline ──────────────────────────────────────────


@router.post(
    "/co-admin/accept",
    response_model=TokenResponse,
    summary="Accept a co-admin invite and set password",
    description=(
        "Called by the /invite/accept frontend page after the invitee clicks the email link. "
        "Validates the GoTrue invite token, sets the password, marks the invite as Accepted, "
        "and returns a session token so the co-admin is logged in immediately."
    ),
)
async def accept_co_admin_invite(
    body: CoAdminAcceptRequest,
    service: CoAdminInviteService = Depends(get_co_admin_invite_service),
) -> TokenResponse:
    return await service.accept(body)


@router.post(
    "/co-admin/decline",
    status_code=200,
    summary="Decline a co-admin invite",
    description=(
        "Called by the /invite/accept frontend page when the invitee clicks Decline. "
        "Deletes the GoTrue user and marks the invite as Declined."
    ),
)
async def decline_co_admin_invite(
    body: CoAdminDeclineRequest,
    service: CoAdminInviteService = Depends(get_co_admin_invite_service),
) -> dict:
    await service.decline(body)
    return {"detail": "Invite declined"}
