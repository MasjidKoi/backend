import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserGoal(Base):
    """A template-led or free-form ibadah goal for a user (PRD 08 §Goals).

    Two kinds, discriminated by ``goal_kind`` (see :class:`GoalKind`):

    - ``quran_quantity`` — a cumulative target (``target_amount`` in ``unit``)
      over the ``[start_date, end_date]`` window. Progress is *journal-fed*:
      summed from the user's matching-unit journal entries, so logging once
      updates the goal. Khatm-in-Ramadan (604 pages) and free-form quantity
      goals. The daily pace is recomputed from remaining/remaining-days at read
      time (US #35) — never stored.
    - ``recurring`` — a daily/weekly check-off habit whose progress comes from
      :class:`GoalCompletion` rows. Daily Ayat al-Kursi, weekly Surah al-Kahf
      (Jumu'ah), and free-form recurring goals.

    The quantity columns and the recurrence column are mutually exclusive by
    kind; validation lives in the schema/service, the domain is fenced by CHECKs.
    """

    __tablename__ = "user_goals"
    __table_args__ = (
        CheckConstraint(
            "goal_kind IN ('quran_quantity', 'recurring')",
            name="ck_goals_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'paused', 'abandoned')",
            name="ck_goals_status",
        ),
        CheckConstraint(
            "unit IS NULL OR unit IN ('pages', 'juz', 'minutes')",
            name="ck_goals_unit",
        ),
        CheckConstraint(
            "recurrence IS NULL OR recurrence IN ('daily', 'weekly')",
            name="ck_goals_recurrence",
        ),
        Index("idx_goals_user_status", "user_id", "status"),
    )

    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    goal_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # The template key this goal was instantiated from, or NULL for free-form.
    template: Mapped[str | None] = mapped_column(String(30), nullable=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active"
    )

    # quran_quantity goals
    target_amount: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(10), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # recurring goals
    recurrence: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    completions: Mapped[list["GoalCompletion"]] = relationship(
        "GoalCompletion",
        back_populates="goal",
        cascade="all, delete-orphan",
        # The FK is ON DELETE CASCADE, so let the DB remove children on a parent
        # delete instead of loading the (lazy='raise') collection ORM-side.
        passive_deletes=True,
        lazy="raise",
    )


class GoalCompletion(Base):
    """A single check-off of a recurring goal on a date (PRD 08 §Goals).

    One row per ``(goal, date)`` — the unique constraint makes check-off
    idempotent, so tapping twice on the same day records one completion. Qur'an-
    quantity goals never produce these rows (their progress is journal-fed).
    """

    __tablename__ = "goal_completions"
    __table_args__ = (
        UniqueConstraint("goal_id", "completion_date", name="uq_goal_completion_date"),
        Index("idx_goal_completions_goal", "goal_id"),
    )

    completion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_goals.goal_id", ondelete="CASCADE"),
        nullable=False,
    )
    completion_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    goal: Mapped["UserGoal"] = relationship(
        "UserGoal", back_populates="completions", lazy="raise"
    )
