import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator

from app.models.enums import Madhab

_MADHAB_ALIASES = {"shafii": "shafi"}  # tolerate the two-i spelling the client used


def _normalize_madhab(v: str | None) -> str | None:
    """Coerce any-case / aliased madhab input to the canonical lowercase ``Madhab``
    enum value the rest of the system uses (prayer_calculator._ASR_MULTIPLIERS,
    platform_settings, prayer_times.madhab). Forgiving on input, canonical on store."""
    if v is None:
        return None
    key = str(v).strip().lower()
    key = _MADHAB_ALIASES.get(key, key)
    valid = {m.value for m in Madhab}
    if key not in valid:
        raise ValueError(f"madhab must be one of {sorted(valid)}")
    return key


MadhabhType = Annotated[str, BeforeValidator(_normalize_madhab)]


class UserProfileResponse(BaseModel):
    user_id: uuid.UUID
    email: str | None
    display_name: str | None
    madhab: str | None
    profile_photo_url: str | None
    is_deleted: bool
    donate_anonymously_by_default: bool
    created_at: datetime
    updated_at: datetime


class FavouriteMasjidResponse(BaseModel):
    masjid_id: uuid.UUID
    name: str
    address: str
    admin_region: str
    verified: bool
    followed_at: datetime


# UserDataExport moved to app/schemas/user_export.py (PRD 09 full export).
