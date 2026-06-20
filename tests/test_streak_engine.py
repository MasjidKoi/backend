"""StreakEngine tests (PRD 08, committed target).

Pure-logic tests — no DB, no fixtures. They pin the executable spec the mobile
streak mirror must also follow: all-five semantics, the noon-next-day Asia/Dhaka
finalization boundary and its edit window, freeze accrual + the two-held cap,
earliest-first auto-application, protected pass-through, longest-vs-current,
empty history, and determinism.
"""

from datetime import date, datetime, timedelta, timezone

from app.services.streak_engine import (
    DayRecord,
    compute_streak,
    finalized_through,
)

# Dhaka 19:00 (UTC 13:00) on 2026-06-16 — past noon, so yesterday is final.
NOW = datetime(2026, 6, 16, 13, 0, tzinfo=timezone.utc)
TODAY = date(2026, 6, 16)
FINAL = date(2026, 6, 15)  # finalized_through(NOW)


def complete_run(end: date, n: int) -> list[DayRecord]:
    """n consecutive all-five-logged days ending at (and including) ``end``."""
    return [
        DayRecord(end - timedelta(days=i), complete=True, protected=False)
        for i in range(n)
    ]


def partial(d: date) -> DayRecord:
    return DayRecord(d, complete=False, protected=False)


def protected(d: date) -> DayRecord:
    return DayRecord(d, complete=False, protected=True)


def test_all_five_extends_and_open_today_does_not_extend_or_break():
    recs = complete_run(FINAL, 5) + [partial(TODAY)]
    assert compute_streak(recs, NOW).current == 5  # today's partial didn't extend

    recs2 = complete_run(FINAL, 5) + [DayRecord(TODAY, complete=True, protected=False)]
    assert compute_streak(recs2, NOW).current == 6  # completing today extends


def test_partial_finalized_day_breaks_the_streak():
    recs = (
        complete_run(date(2026, 6, 13), 3)  # 06-11..06-13 complete
        + [partial(date(2026, 6, 14))]  # finalized partial → break
        + complete_run(FINAL, 1)  # 06-15 complete again
    )
    r = compute_streak(recs, NOW)
    assert r.current == 1
    assert r.longest == 3


def test_noon_finalization_and_edit_window():
    # 06-13,06-14 complete; 06-15 incomplete (no fifth prayer).
    recs = complete_run(date(2026, 6, 14), 2) + [partial(FINAL)]

    before_noon = datetime(2026, 6, 16, 5, 0, tzinfo=timezone.utc)  # Dhaka 11:00
    after_noon = datetime(2026, 6, 16, 6, 0, tzinfo=timezone.utc)  # Dhaka 12:00

    # Before noon 06-15 is still editable (open) → streak holds at 2.
    assert compute_streak(recs, before_noon).current == 2
    # From noon 06-15 is finalized incomplete → streak breaks.
    assert compute_streak(recs, after_noon).current == 0


def test_finalization_boundary_exact():
    assert finalized_through(datetime(2026, 6, 16, 5, 0, tzinfo=timezone.utc)) == date(
        2026, 6, 14
    )  # Dhaka 11:00
    assert finalized_through(datetime(2026, 6, 16, 6, 0, tzinfo=timezone.utc)) == date(
        2026, 6, 15
    )  # Dhaka 12:00 exactly
    assert finalized_through(NOW) == FINAL  # Dhaka 19:00


def test_freeze_accrual_and_two_held_cap():
    r60 = compute_streak(complete_run(FINAL, 60) + [partial(TODAY)], NOW)
    assert r60.current == 60
    assert r60.freezes_held == 2
    assert r60.freezes_applied == 0

    r90 = compute_streak(complete_run(FINAL, 90) + [partial(TODAY)], NOW)
    assert r90.freezes_held == 2  # capped, not 3

    r29 = compute_streak(complete_run(FINAL, 29) + [partial(TODAY)], NOW)
    assert r29.freezes_held == 0


def test_two_freezes_auto_applied_in_order():
    recs = (
        complete_run(date(2026, 6, 13), 60)  # 60 complete → 2 freezes held
        + [partial(date(2026, 6, 14)), partial(FINAL)]  # two finalized slips
        + [partial(TODAY)]
    )
    r = compute_streak(recs, NOW)
    assert r.current == 60  # both slips covered
    assert r.freezes_applied == 2
    assert r.freezes_held == 0


def test_third_slip_breaks_after_two_freezes_spent():
    recs = (
        complete_run(date(2026, 6, 12), 60)  # 2 freezes held
        + [partial(date(2026, 6, 13)), partial(date(2026, 6, 14)), partial(FINAL)]
    )
    r = compute_streak(recs, NOW)
    assert r.current == 0  # third finalized slip breaks
    assert r.freezes_applied == 2
    assert r.longest == 60


def test_protected_passthrough_identical_to_freeze_in_streak_terms():
    base = complete_run(date(2026, 6, 9), 30)  # 30 complete → 1 freeze held
    tail = complete_run(FINAL, 5)  # 06-11..06-15 complete

    exempt = compute_streak(
        base + [protected(date(2026, 6, 10))] + tail + [partial(TODAY)], NOW
    )
    freeze = compute_streak(
        base + [partial(date(2026, 6, 10))] + tail + [partial(TODAY)], NOW
    )

    # The gap passes through identically whether marked exempt or covered by a
    # freeze: same current, same longest — the streak is unbroken in both.
    assert exempt.current == freeze.current == 35
    assert exempt.longest == freeze.longest == 35
    # But an exempt day consumes nothing; a freeze does.
    assert exempt.freezes_applied == 0
    assert freeze.freezes_applied == 1


def test_longest_survives_a_break():
    recs = (
        complete_run(date(2026, 6, 11), 10)  # 10 complete
        + [partial(date(2026, 6, 12))]  # break (no freeze yet)
        + complete_run(FINAL, 3)  # 3 complete
    )
    r = compute_streak(recs, NOW)
    assert r.current == 3
    assert r.longest == 10


def test_empty_history():
    r = compute_streak([], NOW)
    assert r.current == 0
    assert r.longest == 0
    assert r.freezes_held == 0
    assert r.freezes_applied == 0
    assert r.finalized_through == FINAL


def test_determinism_order_independent():
    recs = complete_run(FINAL, 40)
    assert compute_streak(recs, NOW) == compute_streak(list(reversed(recs)), NOW)
