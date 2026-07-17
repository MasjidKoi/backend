"""
FastAPI auth dependencies.

Usage in routes:

    # Any authenticated admin
    @router.get("/something")
    async def handler(user: CurrentUser = Depends(get_current_user)):
        ...

    # Platform admin only (aal2/TOTP not enforced yet — see require_platform_admin)
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

from app.core.config import settings
from app.core.security import CurrentUser, decode_token
from app.models.enums import AdminRole, AuthAssuranceLevel

_bearer = HTTPBearer(auto_error=True)
_bearer_optional = HTTPBearer(auto_error=False)


# ── Base dependency ────────────────────────────────────────────────────────────


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> CurrentUser:
    """
    Extract and validate the Bearer JWT from the Authorization header.
    Returns a typed CurrentUser or raises 401/403.
    """
    return decode_token(credentials.credentials)


def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_optional),
) -> CurrentUser | None:
    """
    Like get_current_user but for endpoints that are public yet attribute the
    caller when signed in. No Authorization header -> None (the request proceeds
    as a guest). A header that IS present is still fully validated by
    decode_token (raising 401/403 on an invalid or role-less token), so a
    malformed token is never silently treated as anonymous.
    """
    if credentials is None:
        return None
    return decode_token(credentials.credentials)


# ── Role guards ───────────────────────────────────────────────────────────────


def require_platform_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    Platform admin — role check only. aal1 or aal2 are BOTH accepted: the
    aal2/TOTP second-factor gate is intentionally NOT enforced yet (TOTP
    enforcement pending). This is the single source of truth for that posture —
    docstrings elsewhere reference it. To require a second factor, uncomment the
    aal2 assertion below (and ensure every platform admin has enrolled TOTP first,
    or they will be locked out of /admin/*).
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


def require_platform_admin_mfa(
    user: CurrentUser = Depends(require_platform_admin),
) -> CurrentUser:
    """Stricter platform-admin guard for money/destructive routes.

    Always enforces the platform-admin role (via require_platform_admin). Only
    when settings.REQUIRE_ADMIN_MFA is True does it additionally require an aal2
    (verified-TOTP) token, returning 403 otherwise. The flag defaults False, so
    behavior is UNCHANGED today — this is wired onto the sensitive routes now so
    MFA can be turned on with a single config flip once every platform admin has
    enrolled TOTP. Do NOT hard-enable here.
    """
    if settings.REQUIRE_ADMIN_MFA and user.aal != AuthAssuranceLevel.AAL2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Two-factor authentication required",
        )
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
