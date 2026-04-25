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


class UserDataExport(BaseModel):
    exported_at: datetime
    user_id: uuid.UUID
    email: str | None
    display_name: str | None
    madhab: str | None
    profile_photo_url: str | None
    created_at: datetime
    followed_masjids: list[FavouriteMasjidResponse]
