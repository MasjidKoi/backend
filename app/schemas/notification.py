import uuid

from pydantic import BaseModel, Field

from app.models.enums import NotificationMode


class FollowModeUpdate(BaseModel):
    notification_mode: NotificationMode


class NotificationPreferencesUpdate(BaseModel):
    """Partial update of the global notification preferences — every field is
    optional; only those sent (``exclude_unset``) are persisted. Covers the daily
    digest hour, the donate-anonymously-by-default toggle (PRD 05 #3), and the
    per-message-type push opt-outs (PRD 05 #4 / PRD 09 #28)."""

    digest_hour: int | None = Field(default=None, ge=0, le=23)
    donate_anonymously_by_default: bool | None = None
    mute_donation_nudge: bool | None = None
    mute_campaign_milestone: bool | None = None
    mute_moderation_outcome: bool | None = None
    mute_photo_outcome: bool | None = None
    mute_promotions: bool | None = None


class FollowedMasjidPreference(BaseModel):
    masjid_id: uuid.UUID
    name: str
    admin_region: str
    latitude: float | None = None
    longitude: float | None = None
    notification_mode: NotificationMode


class NotificationPreferencesResponse(BaseModel):
    """The notification-preferences screen: the user's global toggles plus every
    followed masjid with its per-masjid mode."""

    digest_hour: int
    donate_anonymously_by_default: bool
    mute_donation_nudge: bool
    mute_campaign_milestone: bool
    mute_moderation_outcome: bool
    mute_photo_outcome: bool
    mute_promotions: bool
    masjids: list[FollowedMasjidPreference]
