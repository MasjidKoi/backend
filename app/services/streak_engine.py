"""StreakEngine — the journal-derived prayer streak (PRD 08, gap #17).

Pure and deterministic: ``compute_streak(day_records, now) -> StreakResult``.
No DB, no I/O — the service loads day records and calls this; nothing else
reimplements the rules. The committed tests in ``tests/test_streak_engine.py``
are the executable form of the spec, and the mobile streak mirror follows the
same rules.

The rules (grill decisions, PRD §Streak semantics):

- **All-five-or-nothing.** A day extends the streak only when all five prayers
  are logged. Partial days never extend it.
- **Asia/Dhaka day boundaries**, fixed UTC+6 (no DST).
- **Noon-next-day finalization.** Date ``D`` finalizes at 12:00 Dhaka on ``D+1``.
  Before that the day is *open* — an incomplete open day neither extends nor
  breaks the streak (the user may still log it). After finalization an
  incomplete, unprotected day breaks the streak unless a freeze covers it.
- **Freezes are earned, automatic, never sold, and derived — not stored.** One
  accrues per 30 streak days, at most two held. When a finalized day is
  incomplete and a freeze is held, it is consumed automatically and the streak
  passes through. The same fold that computes the streak computes the freeze
  state, so there is no ledger to drift.
- **Protected (exempt) days pass through.** A stored ``protected`` marker
  (menstruation/postpartum exempt mode, or a client-applied freeze) passes the
  streak through *without consuming a freeze* — no prayers were due. Server-side
  a freeze pass-through and an exemption pass-through are indistinguishable in
  the streak outcome; the reason never leaves the device (PRD §52).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.core.time import DHAKA_TZ

FREEZE_ACCRUAL_DAYS = 30  # one freeze per this many streak days
MAX_FREEZES_HELD = 2
NOON_HOUR = 12  # finalization cutoff, Dhaka wall clock


@dataclass(frozen=True, slots=True)
class DayRecord:
    """One day's streak-relevant state, derived from a journal entry.

    ``complete`` is true when all five prayers were logged that day.
    ``protected`` is the ambiguous protected-day marker (exempt or frozen).
    Days with no journal entry are simply absent from the input.
    """

    date: date
    complete: bool
    protected: bool


@dataclass(frozen=True, slots=True)
class StreakResult:
    current: int
    longest: int
    freezes_held: int
    freezes_applied: int
    finalized_through: date | None


def finalized_through(now: datetime) -> date:
    """The most recent date whose prayer logs are final (streak-locked).

    Date ``D`` finalizes at 12:00 Dhaka on ``D+1``. So before noon today,
    yesterday is still open and the latest final date is two days back; from
    noon onward, yesterday is final. Today is never final.
    """

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    dhaka = now.astimezone(DHAKA_TZ)
    if dhaka.hour < NOON_HOUR:
        return dhaka.date() - timedelta(days=2)
    return dhaka.date() - timedelta(days=1)


def compute_streak(day_records: list[DayRecord], now: datetime) -> StreakResult:
    """Fold the day history into the streak + derived freeze state."""

    if not day_records:
        return StreakResult(
            current=0,
            longest=0,
            freezes_held=0,
            freezes_applied=0,
            finalized_through=finalized_through(now),
        )

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(DHAKA_TZ).date()
    final_through = finalized_through(now)

    by_date = {r.date: r for r in day_records}
    start = min(by_date)

    streak = 0
    longest = 0
    freezes_held = 0
    freezes_applied = 0

    # Walk every calendar day from the first record to today, in order, so
    # freeze accrual and earliest-first auto-application fall out naturally.
    cursor = start
    while cursor <= today:
        rec = by_date.get(cursor)
        complete = rec.complete if rec else False
        protected = rec.protected if rec else False
        is_final = cursor <= final_through

        if complete:
            streak += 1
            if streak % FREEZE_ACCRUAL_DAYS == 0:
                freezes_held = min(MAX_FREEZES_HELD, freezes_held + 1)
            longest = max(longest, streak)
        elif protected:
            # Exempt/frozen day — passes through unbroken, consumes nothing.
            pass
        elif not is_final:
            # Open day (today, or yesterday before noon): not yet decided.
            pass
        elif freezes_held > 0:
            # Auto-apply an earned freeze to a finalized incomplete day.
            freezes_held -= 1
            freezes_applied += 1
        else:
            streak = 0

        cursor += timedelta(days=1)

    return StreakResult(
        current=streak,
        longest=longest,
        freezes_held=freezes_held,
        freezes_applied=freezes_applied,
        finalized_through=final_through,
    )
