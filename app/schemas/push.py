from pydantic import BaseModel, Field


class BroadcastPushRequest(BaseModel):
    """A platform-wide push (PRD 03 PLATFORM_PUSH) — Eid / Ramadan-start / urgent
    notices fanned out to every registered device."""

    title: str = Field(..., min_length=1, max_length=100)
    body: str = Field(..., min_length=1, max_length=240)
    data: dict | None = None


class BroadcastPushResponse(BaseModel):
    devices_notified: int
