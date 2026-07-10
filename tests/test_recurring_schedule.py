"""Tests for RecurringScheduleService — recurring giving as schedule + nudge,
and the stale-pending sweep (PRD 05).

Pure cycle math is tested directly; due-selection, lapse-no-stack, pause/resume/
cancel, and stale-pending expiry are tested against the DB.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.donation import Donation
from app.models.enums import DonationStatus, RecurringScheduleStatus
from app.models.recurring_schedule import RecurringSchedule
from app.repositories.recurring_schedule_repository import RecurringScheduleRepository
from app.schemas.recurring_schedule import (
    RecurringScheduleCreate,
    RecurringScheduleUpdate,
)
from app.services.donation_service import DonationService
from app.services.recurring_schedule_service import (
    RecurringScheduleService,
    _add_months,
    advance_next_due,
    compute_next_due,
)
from tests.conftest import TestSession
from tests.test_donation_ledger import _user

# asyncio_mode="auto" runs async tests automatically; the pure-math tests below
# are sync, so no module-level asyncio mark (it would warn on the sync ones).

UTC = timezone.utc


# ── Pure cycle math (no DB) ──────────────────────────────────────────────────


def test_compute_next_due_weekly_and_nightly():
    base = datetime(2026, 6, 15, 3, 0, tzinfo=UTC)
    assert compute_next_due("weekly", base) == base + timedelta(days=7)
    assert compute_next_due("nightly", base) == base + timedelta(days=1)


def test_compute_next_due_monthly_clamps_day():
    # Jan 31 + 1 month → Feb 28 (2026 is not a leap year).
    assert compute_next_due(
        "monthly", datetime(2026, 1, 31, 9, tzinfo=UTC)
    ) == datetime(2026, 2, 28, 9, tzinfo=UTC)


def test_compute_next_due_monthly_year_rollover():
    assert compute_next_due(
        "monthly", datetime(2026, 12, 15, 9, tzinfo=UTC)
    ) == datetime(2027, 1, 15, 9, tzinfo=UTC)


def test_add_months_leap_year():
    # Jan 31 2028 + 1 month → Feb 29 (2028 is a leap year).
    assert _add_months(datetime(2028, 1, 31, tzinfo=UTC), 1) == datetime(
        2028, 2, 29, tzinfo=UTC
    )


def test_advance_collapses_missed_cycles_no_stack():
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    # next_due 3 weeks in the past — a long lapse.
    stale_due = now - timedelta(weeks=3)
    nxt = advance_next_due("weekly", stale_due, now)
    # Lands on the single next FUTURE cycle, not a backlog.
    assert nxt > now
    assert nxt <= now + timedelta(days=7)


# ── Create / list / pause / resume / cancel ──────────────────────────────────


async def test_create_and_list_schedule(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = RecurringScheduleService(db)

    created = await svc.create(
        _user(uid),
        RecurringScheduleCreate(
            masjid_id=masjid.masjid_id, amount=Decimal("100"), frequency="weekly"
        ),
    )
    assert created.status == "active"
    assert created.frequency == "weekly"
    assert created.next_due_at is not None

    listing = await svc.list_for_user(_user(uid))
    assert len(listing.items) == 1
    assert listing.items[0].schedule_id == created.schedule_id


async def test_pause_then_resume_reanchors_past_due(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = RecurringScheduleService(db)
    created = await svc.create(
        _user(uid),
        RecurringScheduleCreate(
            masjid_id=masjid.masjid_id, amount=Decimal("100"), frequency="weekly"
        ),
    )

    # Force its next_due into the past, then pause + resume.
    sched = await svc.repo.get_owned(created.schedule_id, uid)
    sched.next_due_at = datetime.now(UTC) - timedelta(days=30)
    await svc.repo.commit()

    paused = await svc.update(
        created.schedule_id,
        _user(uid),
        RecurringScheduleUpdate(status=RecurringScheduleStatus.PAUSED),
    )
    assert paused.status == "paused"

    resumed = await svc.update(
        created.schedule_id,
        _user(uid),
        RecurringScheduleUpdate(status=RecurringScheduleStatus.ACTIVE),
    )
    assert resumed.status == "active"
    # Resuming re-anchors a past-due schedule to the future (no backlog nudge).
    assert resumed.next_due_at > datetime.now(UTC)


async def test_cancel_schedule(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = RecurringScheduleService(db)
    created = await svc.create(
        _user(uid),
        RecurringScheduleCreate(
            masjid_id=masjid.masjid_id, amount=Decimal("100"), frequency="monthly"
        ),
    )
    await svc.cancel(created.schedule_id, _user(uid))
    sched = await svc.repo.get_owned(created.schedule_id, uid)
    assert sched.status == "cancelled"


# ── Due selection + nudge sweep ───────────────────────────────────────────────


async def test_run_due_nudges_advances_and_sends_once(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = RecurringScheduleService(db)
    sched = RecurringSchedule(
        user_id=uid,
        masjid_id=masjid.masjid_id,
        category="general",
        amount=Decimal("100.00"),
        frequency="weekly",
        start_date=date(2026, 5, 1),
        next_due_at=datetime(2026, 6, 1, 3, 0, tzinfo=UTC),
        status="active",
    )
    db.add(sched)
    await db.flush()

    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    sent = await svc.run_due_nudges(now=now)
    assert sent == 1

    refreshed = (
        await db.execute(
            select(RecurringSchedule).where(
                RecurringSchedule.schedule_id == sched.schedule_id
            )
        )
    ).scalar_one()
    # Advanced to the next future cycle; not run again on a second sweep.
    assert refreshed.next_due_at > now
    assert await svc.run_due_nudges(now=now) == 0


async def test_due_uses_for_update_skip_locked(db, seed):
    """A concurrent sweep runner must not read schedules another runner has
    already locked — FOR UPDATE SKIP LOCKED makes the second reader skip them
    (never block, never double-nudge/advance). Regression for CODEBASE_AUDIT #25.
    """
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    sched = RecurringSchedule(
        user_id=uid,
        masjid_id=masjid.masjid_id,
        category="general",
        amount=Decimal("100.00"),
        frequency="weekly",
        start_date=date(2026, 5, 1),
        next_due_at=datetime(2026, 6, 1, 3, 0, tzinfo=UTC),
        status="active",
    )
    db.add(sched)
    await seed.commit()  # commit so a second connection sees the row

    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)

    # Runner A locks the due row on a separate connection and holds its txn open.
    async with TestSession() as db2:
        locked = await RecurringScheduleRepository(db2).due(now)
        assert len(locked) == 1  # runner A grabbed it

        # Runner B (concurrent sweep) sees zero due rows — skipped, not blocked.
        also_due = await RecurringScheduleRepository(db).due(now)
        assert also_due == []

        await db2.rollback()  # release the lock before teardown cascade


async def test_bounded_nightly_cancels_past_end_date(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = RecurringScheduleService(db)
    # A last-night nudge whose window ends today → the next cycle is past end_date.
    sched = RecurringSchedule(
        user_id=uid,
        masjid_id=masjid.masjid_id,
        category="general",
        amount=Decimal("100.00"),
        frequency="nightly",
        start_date=date(2026, 6, 14),
        end_date=date(2026, 6, 15),
        next_due_at=datetime(2026, 6, 15, 3, 0, tzinfo=UTC),
        status="active",
    )
    db.add(sched)
    await db.flush()

    await svc.run_due_nudges(now=datetime(2026, 6, 16, 12, 0, tzinfo=UTC))
    refreshed = (
        await db.execute(
            select(RecurringSchedule).where(
                RecurringSchedule.schedule_id == sched.schedule_id
            )
        )
    ).scalar_one()
    assert refreshed.status == "cancelled"


# ── Stale-pending sweep ────────────────────────────────────────────────────


async def test_sweep_stale_pending_fails_only_old(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)

    def _pending(created_at):
        return Donation(
            user_id=uid,
            masjid_id=masjid.masjid_id,
            category="general",
            status=DonationStatus.PENDING.value,
            gross_amount=Decimal("100.00"),
            fee_amount=Decimal("0.00"),
            net_amount=Decimal("100.00"),
            is_anonymous=False,
            created_at=created_at,
        )

    old = _pending(now - timedelta(hours=48))
    recent = _pending(now - timedelta(hours=1))
    db.add_all([old, recent])
    await db.flush()

    swept = await DonationService(db).sweep_stale_pending(now=now, older_than_hours=24)
    assert swept == 1

    rows = {
        d.donation_id: d.status
        for d in (
            await db.execute(select(Donation).where(Donation.user_id == uid))
        ).scalars()
    }
    assert rows[old.donation_id] == DonationStatus.FAILED.value
    assert rows[recent.donation_id] == DonationStatus.PENDING.value
