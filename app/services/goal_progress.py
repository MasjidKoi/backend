"""GoalProgress — pure goal progress + pace computation (PRD 08 §Goals).

Deterministic, no DB, no I/O: the service loads the inputs (journal sum or
completion dates) and calls these; nothing else reimplements the rules. The
committed tests in ``tests/test_goal_progress.py`` are the spec.

Two computations, one per goal kind:

- **Qur'an-quantity** (Khatm, free-form quantity): the daily pace is recomputed
  from *remaining pages over remaining days* every read, so a goal stays
  achievable rather than shaming (US #34, #35). ``current_amount`` is supplied
  by the caller as the journal-fed sum (US #38).
- **Recurring** (daily Ayat al-Kursi, weekly Surah al-Kahf): progress is the
  check-off history. A *period* is one day (daily) or one Saturday→Friday week
  (weekly) — weekly periods culminate on Jumu'ah, so the deadline is Friday and
  the week resets Saturday. The streak is the consecutive run of completed
  periods ending at the current one (an undone current period doesn't yet break
  a live run), mirroring ``_consecutive_fajr`` in ``gamification_service``.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil

from app.models.enums import GoalRecurrence

# Python's date.weekday(): Mon=0 … Sat=5 … Sun=6.
_SATURDAY = 5


@dataclass(frozen=True, slots=True)
class QuranGoalProgress:
    current_amount: int
    remaining: int
    days_remaining: int
    daily_pace: int
    is_complete: bool
    percent: int


@dataclass(frozen=True, slots=True)
class RecurringGoalProgress:
    total_completions: int
    done_this_period: bool
    current_streak: int
    last_completed_on: date | None


def compute_quran_progress(
    target_amount: int,
    current_amount: int,
    today: date,
    end_date: date | None,
) -> QuranGoalProgress:
    """Pace a cumulative Qur'an goal from its journal-fed running total."""

    remaining = max(0, target_amount - current_amount)
    # Days left *including today* while the window is open; 0 once it has passed.
    if end_date is not None and today <= end_date:
        days_remaining = (end_date - today).days + 1
    else:
        days_remaining = 0

    if days_remaining > 0:
        daily_pace = ceil(remaining / days_remaining)
    else:
        # Window over (or none) — whatever's left is owed now, not spread out.
        daily_pace = remaining

    is_complete = current_amount >= target_amount
    # Floor, not round — 602/604 must read 99%, never a premature 100% on an
    # incomplete goal. Reaching target floors to >=100, clamped to 100.
    percent = min(100, current_amount * 100 // target_amount) if target_amount else 100
    return QuranGoalProgress(
        current_amount=current_amount,
        remaining=remaining,
        days_remaining=days_remaining,
        daily_pace=daily_pace,
        is_complete=is_complete,
        percent=percent,
    )


def _period_start(recurrence: str, d: date) -> date:
    """The first day of the period ``d`` falls in: the day itself (daily) or the
    Saturday that opens its week (weekly)."""
    if recurrence == GoalRecurrence.WEEKLY:
        days_since_saturday = (d.weekday() - _SATURDAY) % 7
        return d - timedelta(days=days_since_saturday)
    return d


def _period_step(recurrence: str) -> timedelta:
    return timedelta(days=7 if recurrence == GoalRecurrence.WEEKLY else 1)


def compute_recurring_progress(
    recurrence: str,
    completion_dates: set[date],
    today: date,
) -> RecurringGoalProgress:
    """Fold check-off history into done-this-period + a consecutive-period run."""

    if not completion_dates:
        return RecurringGoalProgress(
            total_completions=0,
            done_this_period=False,
            current_streak=0,
            last_completed_on=None,
        )

    periods = {_period_start(recurrence, d) for d in completion_dates}
    current_period = _period_start(recurrence, today)
    done_this_period = current_period in periods

    step = _period_step(recurrence)
    # Count back from the current period if it's done, else from the previous one
    # — a not-yet-done current period must not zero a streak that's still alive.
    cursor = current_period if done_this_period else current_period - step
    streak = 0
    while cursor in periods:
        streak += 1
        cursor -= step

    return RecurringGoalProgress(
        total_completions=len(completion_dates),
        done_this_period=done_this_period,
        current_streak=streak,
        last_completed_on=max(completion_dates),
    )
