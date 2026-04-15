from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator

from app.models.enums import AdminRole


# ── Request schemas ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TOTPEnrollResponse(BaseModel):
    """Returned when platform admin initiates TOTP enrollment."""
    factor_id: str
    totp_uri: str       # otpauth:// URI — render as QR code in the frontend
    qr_code: str        # base64-encoded PNG for convenience


class TOTPVerifyRequest(BaseModel):
    """Verify TOTP code to upgrade session from aal1 → aal2."""
    factor_id: str
    code: str

    @field_validator("code")
    @classmethod
    def code_must_be_six_digits(cls, v: str) -> str:
        if not v.isdigit() or len(v) != 6:
            raise ValueError("TOTP code must be exactly 6 digits")
        return v


class PasswordResetRequest(BaseModel):
    email: EmailStr


class AdminInviteRequest(BaseModel):
    """
    Platform admin invites a new masjid or madrasha admin.
    GoTrue sends them an invite email; on first login they set their password.
    """
    email: EmailStr
    role: AdminRole

    # Required when role == masjid_admin
    masjid_id: UUID | None = None

    # Required when role == madrasha_admin
    madrasha_id: UUID | None = None

    @field_validator("masjid_id", mode="after")
    @classmethod
    def masjid_id_required_for_masjid_admin(
        cls, v: UUID | None, info
    ) -> UUID | None:
        if info.data.get("role") == AdminRole.MASJID_ADMIN and v is None:
            raise ValueError("masjid_id is required for masjid_admin role")
        return v

    @field_validator("madrasha_id", mode="after")
    @classmethod
    def madrasha_id_required_for_madrasha_admin(
        cls, v: UUID | None, info
    ) -> UUID | None:
        if info.data.get("role") == AdminRole.MADRASHA_ADMIN and v is None:
            raise ValueError("madrasha_id is required for madrasha_admin role")
        return v


# ── Response schemas ──────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str


class AdminInviteResponse(BaseModel):
    gotrue_user_id: UUID
    email: str
    role: AdminRole
