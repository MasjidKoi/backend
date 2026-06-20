"""RecurringScheduleService — recurring giving as a schedule + nudge (PRD 05).

Hosted checkout yields no token, so there is no server-initiated charge: a
schedule fires a push each cycle that opens a prefilled checkout. The cycle math
is pure (``compute_next_due`` / ``advance_next_due``) and lives here; missed
cycles lapse silently and never stack — the sweep collapses any cycles missed
during downtime into the single next future cycle and sends exactly one nudge.
"""

import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.core.time import DHAKA_TZ
from app.models.enums import (
    DonationCategory,
    MasjidStatus,
    PushMessageType,
    RecurringFrequency,
    RecurringScheduleStatus,
)
from app.models.recurring_schedule import RecurringSchedule
from app.repositories.masjid_campaign_repository import MasjidCampaignRepository
from app.repositories.masjid_repository import MasjidRepository
from app.repositories.recurring_schedule_repository import RecurringScheduleRepository
from app.schemas.recurring_schedule import (
    RecurringScheduleCreate,
    RecurringScheduleListResponse,
    RecurringScheduleResponse,
    RecurringScheduleUpdate,
)

logger = logging.getLogger(__name__)

# Reminder fires at this Dhaka hour on a due day.
_NUDGE_HOUR_DHAKA = 9


# ── Pure cycle math (no DB, no I/O — directly unit-tested) ─────────────────────


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def _add_months(dt: datetime, n: int) -> datetime:
    """Add n calendar months, clamping the day to the target month's length
    (Jan 31 + 1 month → Feb 28/29) and preserving the time of day."""
    base = dt.month - 1 + n
    year = dt.year + base // 12
    month = base % 12 + 1
    day = min(dt.day, _days_in_month(year, month))
    return dt.replace(year=year, month=month, day=day)


def compute_next_due(frequency: str, after: datetime) -> datetime:
    """The next cycle's nudge time, one interval after ``after``."""
    if frequency == RecurringFrequency.NIGHTLY:
        return after + timedelta(days=1)
    if frequency == RecurringFrequency.WEEKLY:
        return after + timedelta(days=7)
    if frequency == RecurringFrequency.MONTHLY:
        return _add_months(after, 1)
    raise ValueError(f"Unknown frequency: {frequency!r}")


def advance_next_due(frequency: str, current_due: datetime, now: datetime) -> datetime:
    """Advance to the next FUTURE cycle, collapsing any cycles missed during
    downtime into one — so a lapse never stacks multiple nudges."""
    nxt = compute_next_due(frequency, current_due)
    guard = 0
    while nxt <= now and guard < 100_000:
        nxt = compute_next_due(frequency, nxt)
        guard += 1
    return nxt


def _first_due(start: date) -> datetime:
    """The first nudge time: 09:00 Asia/Dhaka on the start date."""
    return datetime.combine(start, time(_NUDGE_HOUR_DHAKA, 0), tzinfo=DHAKA_TZ)


class RecurringScheduleService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = RecurringScheduleRepository(db)
        self.masjid_repo = MasjidRepository(db)
        self.campaign_repo = MasjidCampaignRepository(db)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    async def create(
        self, user: CurrentUser, data: RecurringScheduleCreate
    ) -> RecurringScheduleResponse:
        masjid_id = data.masjid_id
        category = data.category.value
        campaign_id = data.campaign_id

        if campaign_id is not None:
            campaign = await self.campaign_repo.get_by_id(campaign_id)
            if campaign is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found"
                )
            masjid_id = campaign.masjid_id
            category = DonationCategory.CAMPAIGN.value
        elif category == DonationCategory.CAMPAIGN.value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Campaign category requires a campaign",
            )

        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if masjid is None or masjid.status != MasjidStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Masjid not found or not active",
            )
        if not masjid.donations_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This masjid is not accepting donations",
            )

        start = data.start_date or datetime.now(DHAKA_TZ).date()
        if data.end_date is not None and data.end_date < start:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="end_date must be on or after start_date",
            )

        schedule = RecurringSchedule(
            user_id=user.user_id,
            masjid_id=masjid_id,
            campaign_id=campaign_id,
            category=category,
            amount=data.amount,
            frequency=data.frequency.value,
            start_date=start,
            end_date=data.end_date,
            next_due_at=_first_due(start),
            status=RecurringScheduleStatus.ACTIVE.value,
        )
        await self.repo.add(schedule)
        await self.repo.commit()
        await self.repo.refresh(schedule)
        return _to_response(schedule)

    async def list_for_user(self, user: CurrentUser) -> RecurringScheduleListResponse:
        rows = await self.repo.list_by_user(user.user_id)
        return RecurringScheduleListResponse(items=[_to_response(r) for r in rows])

    async def update(
        self,
        schedule_id: uuid.UUID,
        user: CurrentUser,
        data: RecurringScheduleUpdate,
    ) -> RecurringScheduleResponse:
        schedule = await self._get_owned_or_404(schedule_id, user)
        if data.amount is not None:
            schedule.amount = data.amount
        if data.status is not None:
            new_status = data.status.value
            # Resuming a paused schedule whose next_due is in the past re-anchors
            # it to the next future cycle, so it doesn't fire a backlog.
            if (
                new_status == RecurringScheduleStatus.ACTIVE.value
                and schedule.status != RecurringScheduleStatus.ACTIVE.value
            ):
                now = datetime.now(timezone.utc)
                if schedule.next_due_at <= now:
                    schedule.next_due_at = advance_next_due(
                        schedule.frequency, schedule.next_due_at, now
                    )
            schedule.status = new_status
        await self.repo.commit()
        await self.repo.refresh(schedule)
        return _to_response(schedule)

    async def cancel(self, schedule_id: uuid.UUID, user: CurrentUser) -> None:
        schedule = await self._get_owned_or_404(schedule_id, user)
        schedule.status = RecurringScheduleStatus.CANCELLED.value
        await self.repo.commit()

    async def _get_owned_or_404(
        self, schedule_id: uuid.UUID, user: CurrentUser
    ) -> RecurringSchedule:
        schedule = await self.repo.get_owned(schedule_id, user.user_id)
        if schedule is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found"
            )
        return schedule

    # ── Sweep (called by the scheduler) ────────────────────────────────────────

    async def run_due_nudges(self, now: datetime | None = None) -> int:
        """Send one nudge per due schedule and advance it to the next future
        cycle. Bounded (last-10-nights) schedules that pass their end_date are
        cancelled. Returns the number of nudges sent. Push is best-effort."""
        from app.services.push_service import PushMessage, PushService

        now = now or datetime.now(timezone.utc)
        due = await self.repo.due(now)
        if not due:
            return 0

        push = PushService(self.db)
        sent = 0
        for schedule in due:
            nxt = advance_next_due(schedule.frequency, schedule.next_due_at, now)
            # Past the bounded window → done.
            if schedule.end_date is not None and nxt.date() > schedule.end_date:
                schedule.status = RecurringScheduleStatus.CANCELLED.value
            else:
                schedule.next_due_at = nxt
            sent += 1
        await self.repo.commit()

        for schedule in due:
            try:
                await push.notify_users(
                    [schedule.user_id],
                    PushMessage(
                        message_type=PushMessageType.RECURRING_NUDGE,
                        title="Your scheduled donation is due",
                        body=(
                            f"Tap to give your {schedule.frequency} ৳"
                            f"{schedule.amount:.0f} donation."
                        ),
                        data={
                            "schedule_id": str(schedule.schedule_id),
                            "masjid_id": str(schedule.masjid_id),
                            "amount": f"{schedule.amount:.2f}",
                            "campaign_id": str(schedule.campaign_id)
                            if schedule.campaign_id
                            else "",
                        },
                    ),
                )
            except Exception:
                logger.exception(
                    "recurring nudge push failed for schedule %s",
                    schedule.schedule_id,
                )
        logger.info("Recurring nudges sent: %d", sent)
        return sent


def _to_response(s: RecurringSchedule) -> RecurringScheduleResponse:
    return RecurringScheduleResponse(
        schedule_id=s.schedule_id,
        masjid_id=s.masjid_id,
        campaign_id=s.campaign_id,
        category=s.category,
        amount=s.amount,
        frequency=s.frequency,
        start_date=s.start_date,
        end_date=s.end_date,
        next_due_at=s.next_due_at,
        status=s.status,
        created_at=s.created_at,
    )
