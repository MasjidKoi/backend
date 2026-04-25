"""
JWT verification for GoTrue-issued tokens.

GoTrue signs JWTs with HS256 using GOTRUE_JWT_SECRET. FastAPI verifies
them locally without round-tripping to GoTrue on every request.

JWT payload structure from GoTrue:
{
  "aud": "authenticated",
  "exp": <unix timestamp>,
  "iat": <unix timestamp>,
  "iss": "http://gotrue:9999/",
  "sub": "<user-uuid>",
  "email": "admin@example.com",
  "app_metadata": {
    "provider": "email",
    "role": "platform_admin",       ← our AdminRole
    "masjid_id": "<uuid> | null",   ← set for masjid_admin
    "madrasha_id": "<uuid> | null"  ← set for madrasha_admin
  },
  "user_metadata": {},
  "role": "authenticated",          ← GoTrue internal role (ignore this)
  "aal": "aal1" | "aal2",           ← Authentication Assurance Level
  "session_id": "<uuid>"
}
"""

import logging
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import HTTPException, status

from app.core.config import settings
from app.models.enums import AdminRole, AuthAssuranceLevel

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Decoded, validated identity extracted from a GoTrue JWT."""

    user_id: UUID
    email: str | None
    role: AdminRole
    aal: AuthAssuranceLevel

    # Resource scope — only one will be set depending on role
    masjid_id: UUID | None = None
    madrasha_id: UUID | None = None

    @property
    def is_platform_admin(self) -> bool:
        return self.role == AdminRole.PLATFORM_ADMIN

    @property
    def is_masjid_admin(self) -> bool:
        return self.role == AdminRole.MASJID_ADMIN

    @property
    def is_madrasha_admin(self) -> bool:
        return self.role == AdminRole.MADRASHA_ADMIN

    @property
    def has_mfa(self) -> bool:
        """True if the session was established with a second factor (TOTP)."""
        return self.aal == AuthAssuranceLevel.AAL2


def decode_gotrue_sub(token: str) -> tuple[UUID, str | None]:
    """
    Decode a GoTrue JWT and return (user_id, email) without requiring the role claim.
    Used for co-admin accept/decline — the invite token is minted before Step 2 sets
    app_metadata.role, so decode_token() would reject it with 403 "missing role claim".
    """
    try:
        payload = jwt.decode(
            token,
            settings.GOTRUE_JWT_SECRET,
            algorithms=[_ALGORITHM],
            audience=settings.GOTRUE_JWT_AUD,
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired invite token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return UUID(payload["sub"]), payload.get("email")


def decode_token(token: str) -> CurrentUser:
    """
    Decode and validate a GoTrue-issued JWT.

    Raises HTTPException 401 on any validation failure so callers
    never receive a partially-constructed CurrentUser.
    """
    try:
        payload = jwt.decode(
            token,
            settings.GOTRUE_JWT_SECRET,
            algorithms=[_ALGORITHM],
            audience=settings.GOTRUE_JWT_AUD,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    app_metadata: dict = payload.get("app_metadata") or {}
    raw_role = app_metadata.get("role")

    if not raw_role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token missing role claim",
        )

    try:
        role = AdminRole(raw_role)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Unknown role: {raw_role!r}",
        )

    raw_aal = payload.get("aal", "aal1")
    try:
        aal = AuthAssuranceLevel(raw_aal)
    except ValueError:
        aal = AuthAssuranceLevel.AAL1

    masjid_id: UUID | None = None
    madrasha_id: UUID | None = None

    if raw_mid := app_metadata.get("masjid_id"):
        try:
            masjid_id = UUID(raw_mid)
        except ValueError:
            logger.warning("Invalid masjid_id UUID in token: %s", raw_mid)

    if raw_did := app_metadata.get("madrasha_id"):
        try:
            madrasha_id = UUID(raw_did)
        except ValueError:
            logger.warning("Invalid madrasha_id UUID in token: %s", raw_did)

    return CurrentUser(
        user_id=UUID(payload["sub"]),
        email=payload.get("email"),
        role=role,
        aal=aal,
        masjid_id=masjid_id,
        madrasha_id=madrasha_id,
    )
