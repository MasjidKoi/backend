"""GoalProgress pure-logic tests (PRD 08 §Goals, committed target).

The executable spec for ``app/services/goal_progress.py``: Qur'an-quantity pace
recompute (remaining over remaining-days) and recurring check-off progress
(done-this-period + consecutive-period streak, daily and Saturday-week). No DB —
``today`` is an explicit argument, dates anchored to a known Friday (2026-06-19)
so the weekly Saturday→Friday boundary is unambiguous.
"""

from datetime import date, timedelta

from app.models.enums import GoalRecurrence
from app.services.goal_progress import (
    compute_quran_progress,
    compute_recurring_progress,
)

FRIDAY = date(2026, 6, 19)  # week opens Sat 2026-06-13
SATURDAY = date(2026, 6, 20)  # opens the next week

DAILY = GoalRecurrence.DAILY.value
WEEKLY = GoalRecurrence.WEEKLY.value


# ── Qur'an-quantity pace ──────────────────────────────────────────────────────


def test_quran_pace_full_window():
    today, end = date(2026, 6, 1), date(2026, 6, 10)  # 10 inclusive days
    p = compute_quran_progress(
        target_amount=100, current_amount=0, today=today, end_date=end
    )
    assert p.remaining == 100
    assert p.days_remaining == 10
    assert p.daily_pace == 10  # ceil(100/10)
    assert p.percent == 0
    assert p.is_complete is False


def test_quran_pace_drops_as_progress_is_made():
    today, end = date(2026, 6, 1), date(2026, 6, 10)
    p = compute_quran_progress(100, 50, today, end)
    assert p.remaining == 50
    assert p.daily_pace == 5  # ceil(50/10) — eases as the total climbs
    assert p.percent == 50


def test_quran_pace_rises_as_days_run_out():
    # Same 50 left, but only 5 days remain → the daily ask recomputes upward.
    p = compute_quran_progress(
        100, 50, today=date(2026, 6, 1), end_date=date(2026, 6, 5)
    )
    assert p.days_remaining == 5
    assert p.daily_pace == 10  # ceil(50/5)


def test_quran_complete_and_over_target():
    done = compute_quran_progress(100, 100, date(2026, 6, 1), date(2026, 6, 10))
    assert done.is_complete is True
    assert done.remaining == 0
    assert done.daily_pace == 0
    assert done.percent == 100

    over = compute_quran_progress(100, 130, date(2026, 6, 1), date(2026, 6, 10))
    assert over.is_complete is True
    assert over.remaining == 0
    assert over.percent == 100  # capped, never >100


def test_quran_percent_floors_not_rounds():
    # 602/604 = 99.67% must read 99, never a premature 100 on an incomplete goal.
    p = compute_quran_progress(604, 602, date(2026, 6, 1), date(2026, 6, 30))
    assert p.percent == 99
    assert p.is_complete is False


def test_quran_window_passed_owes_remainder_now():
    p = compute_quran_progress(
        100, 80, today=date(2026, 6, 11), end_date=date(2026, 6, 10)
    )
    assert p.days_remaining == 0
    assert p.daily_pace == 20  # whatever's left, not spread out
    assert p.is_complete is False


# ── Recurring: daily ──────────────────────────────────────────────────────────


def test_recurring_empty():
    p = compute_recurring_progress(DAILY, set(), FRIDAY)
    assert p == p.__class__(
        total_completions=0,
        done_this_period=False,
        current_streak=0,
        last_completed_on=None,
    )


def test_daily_consecutive_streak():
    dates = {FRIDAY, FRIDAY - timedelta(days=1), FRIDAY - timedelta(days=2)}
    p = compute_recurring_progress(DAILY, dates, FRIDAY)
    assert p.done_this_period is True
    assert p.current_streak == 3
    assert p.total_completions == 3
    assert p.last_completed_on == FRIDAY


def test_daily_gap_breaks_streak():
    dates = {FRIDAY, FRIDAY - timedelta(days=2)}  # yesterday missing
    p = compute_recurring_progress(DAILY, dates, FRIDAY)
    assert p.done_this_period is True
    assert p.current_streak == 1


def test_daily_today_not_done_keeps_live_streak():
    # Today unlogged, but the run through yesterday is still alive.
    dates = {FRIDAY - timedelta(days=1), FRIDAY - timedelta(days=2)}
    p = compute_recurring_progress(DAILY, dates, FRIDAY)
    assert p.done_this_period is False
    assert p.current_streak == 2


# ── Recurring: weekly (Saturday → Friday) ─────────────────────────────────────


def test_weekly_consecutive_weeks():
    dates = {FRIDAY, FRIDAY - timedelta(days=7), FRIDAY - timedelta(days=14)}
    p = compute_recurring_progress(WEEKLY, dates, FRIDAY)
    assert p.done_this_period is True
    assert p.current_streak == 3


def test_weekly_same_week_counts_once():
    # A Wednesday completion sits in the same Sat→Fri week as Friday's "today".
    wednesday = date(2026, 6, 17)
    p = compute_recurring_progress(WEEKLY, {wednesday}, FRIDAY)
    assert p.done_this_period is True
    assert p.current_streak == 1
    assert p.total_completions == 1


def test_weekly_new_week_not_yet_done_keeps_streak():
    # Saturday opens a fresh week; last week's completion keeps the run alive.
    last_week = {FRIDAY}
    p = compute_recurring_progress(WEEKLY, last_week, SATURDAY)
    assert p.done_this_period is False
    assert p.current_streak == 1
