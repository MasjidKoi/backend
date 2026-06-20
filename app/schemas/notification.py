import uuid

from pydantic import BaseModel, Field

from app.models.enums import NotificationMode


class FollowModeUpdate(BaseModel):
    notification_mode: NotificationMode


class DigestHourUpdate(BaseModel):
    digest_hour: int = Field(..., ge=0, le=23)


class FollowedMasjidPreference(BaseModel):
    masjid_id: uuid.UUID
    name: str
    notification_mode: NotificationMode


class NotificationPreferencesResponse(BaseModel):
    """The notification-preferences screen: the user's digest hour (Asia/Dhaka)
    plus every followed masjid with its per-masjid mode."""

    digest_hour: int
    masjids: list[FollowedMasjidPreference]
