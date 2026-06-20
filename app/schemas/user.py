import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

MadhabhType = Literal["Hanafi", "Shafii", "Maliki", "Hanbali"]


class UserProfileResponse(BaseModel):
    user_id: uuid.UUID
    email: str | None
    display_name: str | None
    madhab: str | None
    profile_photo_url: str | None
    is_deleted: bool
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
