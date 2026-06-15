import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ── Create ───────────────────────────────────────────────────────────────────


class QuestionCreate(BaseModel):
    """Consumer question. Length-validated to keep the queue meaningful."""

    question: str = Field(..., min_length=10, max_length=1000)


# ── Answer ───────────────────────────────────────────────────────────────────


class QuestionAnswer(BaseModel):
    """Moderator answer body."""

    answer: str = Field(..., min_length=1, max_length=5000)


# ── Public ───────────────────────────────────────────────────────────────────


class QuestionPublic(BaseModel):
    """Public view of an answered question (no asker identity)."""

    model_config = ConfigDict(from_attributes=True)
    question_id: uuid.UUID
    masjid_id: uuid.UUID
    question: str
    answer: str | None
    answered_at: datetime | None
    created_at: datetime


class QuestionPublicListResponse(BaseModel):
    items: list[QuestionPublic]
    total: int
    page: int
    page_size: int


# ── Asker (POST response + GET /me/questions) ──────────────────────────────────


class MyQuestionResponse(BaseModel):
    """The asker's own view — includes moderation status + timestamps."""

    model_config = ConfigDict(from_attributes=True)
    question_id: uuid.UUID
    masjid_id: uuid.UUID
    question: str
    status: str
    answer: str | None
    answered_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ── Moderation (masjid admin + platform admin) ─────────────────────────────────


class QuestionModerationResponse(MyQuestionResponse):
    """Moderator view — adds the asker + answerer identity."""

    asker_user_id: uuid.UUID
    answered_by: uuid.UUID | None
    answer_author_role: str | None


class QuestionModerationListResponse(BaseModel):
    items: list[QuestionModerationResponse]
    total: int
    page: int
    page_size: int
