import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlatformSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    settings_id: uuid.UUID
    default_madhab: str
    default_calc_method: str
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
    supported_countries: list[str] | None = None
    reviews_enabled: bool | None = None
    checkins_enabled: bool | None = None
    platform_name: str | None = Field(default=None, max_length=100)
    maintenance_mode: bool | None = None
    maintenance_message: str | None = None
    terms_of_service: str | None = None
    privacy_policy: str | None = None
    terms_version: str | None = Field(default=None, max_length=20)
