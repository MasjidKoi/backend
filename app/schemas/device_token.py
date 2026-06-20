from pydantic import BaseModel, Field

from app.models.enums import DevicePlatform


class DeviceTokenRegister(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)
    platform: DevicePlatform
