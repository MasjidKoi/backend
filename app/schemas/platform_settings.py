import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlatformSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    settings_id: uuid.UUID
    default_madhab: str
    default_calc_method: str
    hijri_offset_days: int
    supported_countries: list[str] | None
    reviews_enabled: bool
    checkins_enabled: bool
    platform_name: str
    maintenance_mode: bool
    maintenance_message: str | None
    terms_of_service: str | None
    privacy_policy: str | None
    terms_version: str | None
    updated_at: datetime
    updated_by_email: str | None


class PlatformSettingsUpdate(BaseModel):
    default_madhab: str | None = Field(
        default=None, pattern="^(hanafi|shafi|maliki|hanbali)$"
    )
    default_calc_method: str | None = None
    # Hijri-date display correction, −2…+2 days. None leaves it unchanged.
    hijri_offset_days: int | None = Field(default=None, ge=-2, le=2)
    supported_countries: list[str] | None = None
    reviews_enabled: bool | None = None
    checkins_enabled: bool | None = None
    platform_name: str | None = Field(default=None, max_length=100)
    maintenance_mode: bool | None = None
    maintenance_message: str | None = None
    terms_of_service: str | None = None
    privacy_policy: str | None = None
    terms_version: str | None = Field(default=None, max_length=20)


class AppConfigResponse(BaseModel):
    """Public, unauthenticated app configuration — the read-only subset of
    platform settings mobile clients need at startup (PRD 03). Deliberately
    omits admin-only / sensitive fields (terms text, feature internals)."""

    model_config = ConfigDict(from_attributes=True)

    hijri_offset_days: int
    default_calc_method: str
    default_madhab: str
    platform_name: str
    maintenance_mode: bool
    maintenance_message: str | None
