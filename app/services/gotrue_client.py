"""
Async HTTP client for GoTrue admin operations.

FastAPI calls GoTrue's admin API using a service_role JWT.
This client is a thin wrapper around httpx — it does NOT
re-implement auth logic, it delegates to GoTrue.

All methods raise HTTPException so errors propagate cleanly
through FastAPI's error handling.
"""

import logging
from uuid import UUID

import httpx
from fastapi import HTTPException, status

from app.core.config import settings
from app.models.enums import AdminRole

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


def _admin_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.GOTRUE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "apikey": settings.GOTRUE_SERVICE_ROLE_KEY,
    }


def _raise_for_gotrue(response: httpx.Response, context: str) -> None:
    if response.is_success:
        return
    body = response.text
    logger.error("GoTrue %s failed: %s %s", context, response.status_code, body)
    raise HTTPException(
        status_code=response.status_code,
        detail=f"GoTrue error during {context}: {body}",
    )


class GoTrueClient:
    """
    Wraps GoTrue endpoints needed by MasjidKoi.

    Instantiate once per request (or use as a singleton for read-only calls).
    Uses a fresh httpx.AsyncClient per call to stay compatible with
    NullPool / short-lived connection philosophy.
    """

    def __init__(self) -> None:
        self._base = settings.gotrue_base_url

    # ── Token endpoints (proxied for clients) ─────────────────────────────────

    async def login_with_password(
        self, email: str, password: str
    ) -> dict:
        """POST /token?grant_type=password"""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base}/token?grant_type=password",
                json={"email": email, "password": password},
            )
        _raise_for_gotrue(resp, "password login")
        return resp.json()

    async def refresh_token(self, refresh_token: str) -> dict:
        """POST /token?grant_type=refresh_token"""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base}/token?grant_type=refresh_token",
                json={"refresh_token": refresh_token},
            )
        _raise_for_gotrue(resp, "token refresh")
        return resp.json()

    async def logout(self, access_token: str) -> None:
        """POST /logout — revokes all refresh tokens for the user."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base}/logout",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        _raise_for_gotrue(resp, "logout")

    async def request_password_reset(self, email: str) -> None:
        """POST /recover"""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base}/recover",
                json={"email": email},
            )
        _raise_for_gotrue(resp, "password reset request")

    # ── Admin endpoints (service_role) ────────────────────────────────────────

    async def create_admin_user(
        self,
        email: str,
        role: AdminRole,
        masjid_id: UUID | None = None,
        madrasha_id: UUID | None = None,
        send_invite: bool = True,
    ) -> dict:
        """
        POST /admin/users — create a new admin user in GoTrue.

        Sets app_metadata so the role and resource scope appear in every JWT
        issued to this user. app_metadata is immutable by the user themselves.
        """
        app_metadata: dict = {
            "role": str(role),
            "masjid_id": str(masjid_id) if masjid_id else None,
            "madrasha_id": str(madrasha_id) if madrasha_id else None,
        }

        payload: dict = {
            "email": email,
            "app_metadata": app_metadata,
            "email_confirm": not send_invite,
        }

        if send_invite:
            # GoTrue sends an invite email; user sets password on first login
            payload["email_confirm"] = False
            payload["invite"] = True

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base}/admin/users",
                json=payload,
                headers=_admin_headers(),
            )
        _raise_for_gotrue(resp, "create admin user")
        return resp.json()

    async def update_user_app_metadata(
        self,
        gotrue_user_id: UUID,
        app_metadata: dict,
    ) -> dict:
        """PUT /admin/users/{id} — update app_metadata (role/scopes)."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.put(
                f"{self._base}/admin/users/{gotrue_user_id}",
                json={"app_metadata": app_metadata},
                headers=_admin_headers(),
            )
        _raise_for_gotrue(resp, "update user app_metadata")
        return resp.json()

    async def ban_user(self, gotrue_user_id: UUID, duration: str = "876000h") -> None:
        """
        PUT /admin/users/{id} with ban_duration.
        Default duration is effectively permanent (~100 years).
        Pass "none" to unban.
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.put(
                f"{self._base}/admin/users/{gotrue_user_id}",
                json={"ban_duration": duration},
                headers=_admin_headers(),
            )
        _raise_for_gotrue(resp, "ban user")

    async def delete_user(self, gotrue_user_id: UUID) -> None:
        """DELETE /admin/users/{id}"""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.delete(
                f"{self._base}/admin/users/{gotrue_user_id}",
                headers=_admin_headers(),
            )
        _raise_for_gotrue(resp, "delete user")

    # ── MFA / TOTP (platform_admin enrollment) ────────────────────────────────

    async def enroll_totp(self, access_token: str) -> dict:
        """
        POST /factors — enroll a TOTP factor for the authenticated user.
        Returns factor_id, totp_uri (for QR code), and qr_code (base64 PNG).
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base}/factors",
                json={"factor_type": "totp", "friendly_name": "MasjidKoi Authenticator"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        _raise_for_gotrue(resp, "TOTP enroll")
        return resp.json()

    async def _challenge_totp(self, access_token: str, factor_id: str) -> str:
        """
        POST /factors/{factor_id}/challenge — create a challenge for TOTP verification.
        Returns the challenge_id needed for verify.
        GoTrue requires this challenge step before verify.
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base}/factors/{factor_id}/challenge",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        _raise_for_gotrue(resp, "TOTP challenge")
        return resp.json()["id"]

    async def verify_totp(
        self, access_token: str, factor_id: str, code: str
    ) -> dict:
        """
        Full TOTP verification flow:
          1. POST /factors/{id}/challenge  → get challenge_id
          2. POST /factors/{id}/verify     → submit code + challenge_id

        On success GoTrue returns a new JWT with aal=aal2.
        """
        challenge_id = await self._challenge_totp(access_token, factor_id)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base}/factors/{factor_id}/verify",
                json={"challenge_id": challenge_id, "code": code},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        _raise_for_gotrue(resp, "TOTP verify")
        return resp.json()

    async def update_user_password(self, access_token: str, password: str) -> None:
        """
        PUT /user — set or update password for the currently authenticated user.
        Used by invited users to set their password on first login.
        The access_token here is the invite token from the email link hash fragment.
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.put(
                f"{self._base}/user",
                json={"password": password},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        _raise_for_gotrue(resp, "update user password")


# ── Singleton for use in dependencies ─────────────────────────────────────────

gotrue = GoTrueClient()
