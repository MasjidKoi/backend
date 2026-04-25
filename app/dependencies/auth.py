"""
FastAPI auth dependencies.

Usage in routes:

    # Any authenticated admin
    @router.get("/something")
    async def handler(user: CurrentUser = Depends(get_current_user)):
        ...

    # Platform admin only (requires TOTP / aal2)
    @router.post("/admin/masjids")
    async def create_masjid(user: CurrentUser = Depends(require_platform_admin)):
        ...

    # Masjid admin (or platform admin acting on behalf)
    @router.patch("/masjids/{id}/profile")
    async def update_profile(
        id: UUID,
        user: CurrentUser = Depends(require_masjid_admin),
    ):
        if not user.is_platform_admin and user.masjid_id != id:
            raise HTTPException(403, "Access restricted to own masjid")
        ...
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import CurrentUser, decode_token
from app.models.enums import AdminRole, AuthAssuranceLevel

_bearer = HTTPBearer(auto_error=True)


# ── Base dependency ────────────────────────────────────────────────────────────


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> CurrentUser:
    """
    Extract and validate the Bearer JWT from the Authorization header.
    Returns a typed CurrentUser or raises 401/403.
    """
    return decode_token(credentials.credentials)


# ── Role guards ───────────────────────────────────────────────────────────────


def require_platform_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    Platform admin — aal1 or aal2 accepted (TOTP disabled for now).
    """
    if user.role != AdminRole.PLATFORM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin access required",
        )
    # TODO: re-enable aal2 check when TOTP is stable
    # if user.aal != AuthAssuranceLevel.AAL2:
    #     raise HTTPException(status_code=403, detail="Two-factor authentication required")
    return user


def require_masjid_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    Masjid admin or platform admin.

    Routes using this dependency MUST additionally verify that
    `user.masjid_id == <path param>` unless the caller is a platform admin.
    """
    if user.role not in (AdminRole.PLATFORM_ADMIN, AdminRole.MASJID_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Masjid admin access required",
        )
    return user


def require_madrasha_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    Madrasha admin or platform admin.

    Routes using this dependency MUST additionally verify that
    `user.madrasha_id == <path param>` unless the caller is a platform admin.
    """
    if user.role not in (AdminRole.PLATFORM_ADMIN, AdminRole.MADRASHA_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Madrasha admin access required",
        )
    return user


def require_any_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Any valid admin role — used for shared read endpoints."""
    return user
