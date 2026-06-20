import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    GoalKind,
    GoalRecurrence,
    GoalStatus,
    GoalTemplateKey,
    QuranUnit,
)

# Same bound as the journal's Qur'an amount — within SMALLINT, far above any
# real target (604 pages / 30 juz / minutes).
_MAX_TARGET = 10000


class GoalCreate(BaseModel):
    """Create a free-form goal.

    ``quran_quantity`` goals require a target, unit, and date window; ``recurring``
    goals require a recurrence. Fields that don't belong to the chosen kind are
    rejected so a malformed goal can't be persisted.
    """

    goal_kind: GoalKind
    title: str = Field(..., min_length=1, max_length=120)
    # quran_quantity
    target_amount: int | None = Field(default=None, ge=1, le=_MAX_TARGET)
    unit: QuranUnit | None = None
    start_date: date | None = None
    end_date: date | None = None
    # recurring
    recurrence: GoalRecurrence | None = None

    @model_validator(mode="after")
    def _check_kind_fields(self) -> "GoalCreate":
        quantity_fields = {
            "target_amount": self.target_amount,
            "unit": self.unit,
            "start_date": self.start_date,
            "end_date": self.end_date,
        }
        if self.goal_kind == GoalKind.QURAN_QUANTITY:
            missing = [n for n, v in quantity_fields.items() if v is None]
            if missing:
                raise ValueError(f"quran_quantity goals require: {', '.join(missing)}")
            if self.end_date < self.start_date:  # type: ignore[operator]
                raise ValueError("end_date must be on or after start_date")
            if self.recurrence is not None:
                raise ValueError("recurrence is not valid for a quran_quantity goal")
        else:  # recurring
            if self.recurrence is None:
                raise ValueError("recurring goals require a recurrence")
            extra = [n for n, v in quantity_fields.items() if v is not None]
            if extra:
                raise ValueError(
                    f"these fields are not valid for a recurring goal: "
                    f"{', '.join(extra)}"
                )
        return self


class GoalTemplateCreate(BaseModel):
    """Instantiate a preset template. ``start_date``/``end_date`` are required
    only for date-bound templates (Khatm-in-Ramadan); the service enforces that
    against the template definition."""

    template: GoalTemplateKey
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def _check_window(self) -> "GoalTemplateCreate":
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must be on or after start_date")
        return self


class GoalStatusUpdate(BaseModel):
    """Pause, resume, or abandon a goal — and/or rename it (US #39)."""

    status: GoalStatus | None = None
    title: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def _at_least_one(self) -> "GoalStatusUpdate":
        if self.status is None and self.title is None:
            raise ValueError("provide at least one of: status, title")
        return self


class GoalCompletionCreate(BaseModel):
    """Check off a recurring goal. Defaults to today (Asia/Dhaka) when omitted."""

    completion_date: date | None = None


class GoalProgress(BaseModel):
    """Computed progress. Which fields are populated depends on ``kind``:
    quran_quantity fills the pace block; recurring fills the check-off block."""

    kind: GoalKind
    # quran_quantity
    current_amount: int | None = None
    target_amount: int | None = None
    remaining: int | None = None
    days_remaining: int | None = None
    daily_pace: int | None = None
    is_complete: bool | None = None
    percent: int | None = None
    # recurring
    total_completions: int | None = None
    done_this_period: bool | None = None
    current_streak: int | None = None
    last_completed_on: date | None = None


class GoalResponse(BaseModel):
    goal_id: uuid.UUID
    goal_kind: GoalKind
    template: str | None
    title: str
    status: GoalStatus
    target_amount: int | None
    unit: QuranUnit | None
    start_date: date | None
    end_date: date | None
    recurrence: GoalRecurrence | None
    created_at: datetime
    updated_at: datetime
    progress: GoalProgress


class GoalListResponse(BaseModel):
    items: list[GoalResponse]
    total: int
