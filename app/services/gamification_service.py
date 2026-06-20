import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.core.time import DHAKA_TZ
from app.models.enums import MasjidStatus
from app.models.user_badge import UserBadge
from app.models.user_journal_entry import UserJournalEntry
from app.repositories.masjid_repository import MasjidRepository
from app.repositories.user_badge_repository import UserBadgeRepository
from app.repositories.user_checkin_repository import UserCheckinRepository
from app.repositories.user_journal_repository import UserJournalRepository
from app.schemas.gamification import (
    BadgeFamilyProgress,
    BadgeResponse,
    CheckInCreate,
    CheckInHistoryItem,
    CheckInHistoryResponse,
    CheckInResponse,
    EarnedTier,
    JournalEntryResponse,
    JournalEntryUpsert,
    JournalListResponse,
    PrayerSet,
    QuranProgress,
    StreakResponse,
)
from app.services.badge_engine import (
    BADGE_THRESHOLDS,
    BadgeCounters,
    counter_for,
    evaluate,
)
from app.services.streak_engine import (
    DayRecord,
    compute_streak,
    finalized_through,
)

# Streak/badge folds look back at most this many days — ~10 years, beyond any
# human prayer streak, so it never truncates a real current/longest streak; it
# only bounds the query for pathologically old accounts.
LOOKBACK_DAYS = 3700

PRAYER_FIELDS = ("fajr", "dhuhr", "asr", "maghrib", "isha")


class GamificationService:
    def __init__(self, db: AsyncSession) -> None:
        self.checkin_repo = UserCheckinRepository(db)
        self.badge_repo = UserBadgeRepository(db)
        self.journal_repo = UserJournalRepository(db)
        self.masjid_repo = MasjidRepository(db)

    # ── Check-ins ───────────────────────────────────────────────────────────

    async def checkin(
        self,
        masjid_id: uuid.UUID,
        data: CheckInCreate,
        user: CurrentUser,
    ) -> CheckInResponse:
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if not masjid or masjid.status != MasjidStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Masjid not found or not active",
            )

        user_point = func.ST_GeographyFromText(
            f"SRID=4326;POINT({data.longitude} {data.latitude})"
        )
        within = await self.checkin_repo.is_within_100m(masjid_id, user_point)
        if not within:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You must be within 100 m of the masjid to check in",
            )

        checkin = await self.checkin_repo.create(user.user_id, masjid_id)
        new_badges = await self._evaluate_badges(user.user_id)
        await self.checkin_repo.commit()

        return CheckInResponse(
            checkin_id=checkin.checkin_id,
            masjid_id=checkin.masjid_id,
            checked_in_at=checkin.checked_in_at,
            new_badges=[_to_badge_response(b) for b in new_badges],
        )

    async def list_checkins(
        self,
        user: CurrentUser,
        page: int,
        page_size: int,
    ) -> CheckInHistoryResponse:
        rows, total = await self.checkin_repo.list_by_user(
            user.user_id, offset=(page - 1) * page_size, limit=page_size
        )
        return CheckInHistoryResponse(
            items=[
                CheckInHistoryItem(
                    checkin_id=c.checkin_id,
                    masjid_id=c.masjid_id,
                    masjid_name=name,
                    checked_in_at=c.checked_in_at,
                )
                for c, name in rows
            ],
            total_checkins=total,
            page=page,
            page_size=page_size,
        )

    # ── Streak ──────────────────────────────────────────────────────────────

    async def get_streak(self, user: CurrentUser) -> StreakResponse:
        now = datetime.now(timezone.utc)
        records = await self._load_day_records(user.user_id, now)
        result = compute_streak(records, now)
        return StreakResponse(
            current=result.current,
            longest=result.longest,
            freezes_held=result.freezes_held,
            freezes_applied=result.freezes_applied,
        )

    # ── Badges ──────────────────────────────────────────────────────────────

    async def list_badges(self, user: CurrentUser) -> list[BadgeFamilyProgress]:
        now = datetime.now(timezone.utc)
        counters = await self._compute_counters(user.user_id, now)
        badges = await self.badge_repo.list_by_user(user.user_id)

        progress: list[BadgeFamilyProgress] = []
        for family, thresholds in BADGE_THRESHOLDS.items():
            value = counter_for(family, counters)
            earned = sorted(
                (b for b in badges if b.badge_type == family.value),
                key=lambda b: b.tier,
            )
            current_tier = max((b.tier for b in earned), default=0)
            next_threshold = (
                thresholds[current_tier] if current_tier < len(thresholds) else None
            )
            progress.append(
                BadgeFamilyProgress(
                    badge_type=family.value,
                    current_value=value,
                    current_tier=current_tier,
                    next_threshold=next_threshold,
                    earned=[
                        EarnedTier(tier=b.tier, earned_at=b.earned_at) for b in earned
                    ],
                )
            )
        return progress

    # ── Journal ─────────────────────────────────────────────────────────────

    async def list_journal(
        self,
        user: CurrentUser,
        page: int,
        page_size: int,
        date_from,
        date_to,
    ) -> JournalListResponse:
        rows, total = await self.journal_repo.list_by_user(
            user.user_id,
            offset=(page - 1) * page_size,
            limit=page_size,
            date_from=date_from,
            date_to=date_to,
        )
        return JournalListResponse(
            items=[_to_journal_response(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def upsert_journal(
        self,
        data: JournalEntryUpsert,
        user: CurrentUser,
    ) -> JournalEntryResponse:
        now = datetime.now(timezone.utc)
        provided = data.model_dump(exclude_unset=True)
        touches_prayers = "prayers" in provided
        touches_protected = "is_protected" in provided

        # Backfill window: once a date is finalized (streak-locked), its prayer
        # logs and protected marker are immutable — but notes and Qur'an stay
        # editable indefinitely (PRD §Streak semantics, US #33).
        if data.entry_date <= finalized_through(now) and (
            touches_prayers or touches_protected
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Prayer logs for this date are finalized and can no longer "
                    "be edited. Notes and Qur'an progress remain editable."
                ),
            )

        existing = await self.journal_repo.get_by_user_date(
            user.user_id, data.entry_date
        )
        entry = existing or UserJournalEntry(
            user_id=user.user_id, entry_date=data.entry_date
        )

        if touches_prayers:
            prayers = data.prayers or PrayerSet()
            for field in PRAYER_FIELDS:
                setattr(entry, field, getattr(prayers, field))
        if "quran" in provided:
            entry.quran_amount = data.quran.amount if data.quran else None
            entry.quran_unit = data.quran.unit.value if data.quran else None
        if "notes" in provided:
            entry.notes = data.notes
        if touches_protected:
            entry.is_protected = bool(data.is_protected)

        if existing is None:
            await self.journal_repo.add(entry)
        await self.journal_repo.db.flush()

        # Only a prayer change can move a journal-derived badge counter
        # (Fajr Warrior). Notes/Qur'an edits skip the re-evaluation entirely.
        if touches_prayers:
            await self._evaluate_badges(user.user_id, now=now)
        await self.journal_repo.commit()
        # Refresh in async context so the response carries the server-computed
        # created_at/updated_at (the onupdate=now() column is postfetch-expired
        # after the UPDATE; reading it lazily would attempt sync IO).
        await self.journal_repo.db.refresh(entry)
        return _to_journal_response(entry)

    # ── Internals ───────────────────────────────────────────────────────────

    async def _load_day_records(
        self, user_id: uuid.UUID, now: datetime
    ) -> list[DayRecord]:
        since = now.astimezone(DHAKA_TZ).date() - timedelta(days=LOOKBACK_DAYS)
        entries = await self.journal_repo.get_day_records(user_id, since=since)
        return [
            DayRecord(
                date=e.entry_date,
                complete=all(getattr(e, f) for f in PRAYER_FIELDS),
                protected=e.is_protected,
            )
            for e in entries
        ]

    async def _compute_counters(
        self, user_id: uuid.UUID, now: datetime
    ) -> BadgeCounters:
        since = now.astimezone(DHAKA_TZ).date() - timedelta(days=LOOKBACK_DAYS)
        entries = await self.journal_repo.get_day_records(user_id, since=since)
        today = now.astimezone(DHAKA_TZ).date()
        return BadgeCounters(
            consecutive_fajr_days=_consecutive_fajr(entries, today),
            # Dormant until the donation system (#11) lands.
            consecutive_giving_months=0,
            # Verified-contribution points. v0 counts check-ins ONLY; accepted
            # info reports and approved community photos are part of the Community
            # Pillar criterion (see BadgeEngine) but are not yet summed here.
            contribution_points=await self.checkin_repo.count_by_user(user_id),
        )

    async def _evaluate_badges(
        self, user_id: uuid.UUID, now: datetime | None = None
    ) -> list[UserBadge]:
        now = now or datetime.now(timezone.utc)
        counters = await self._compute_counters(user_id, now)
        already = await self.badge_repo.awarded_set(user_id)
        awards = evaluate(counters, already)

        created: list[UserBadge] = []
        for a in awards:
            # A concurrent request may have awarded the same (user, type, tier);
            # isolate each insert in a savepoint so a unique-violation skips just
            # that badge instead of aborting the caller's journal/check-in write.
            try:
                async with self.badge_repo.db.begin_nested():
                    created.append(
                        await self.badge_repo.award(user_id, a.badge_type, a.tier)
                    )
            except IntegrityError:
                continue
        return created


def _consecutive_fajr(entries: list[UserJournalEntry], today) -> int:
    """The contiguous run of Fajr-logged days ending at the most recent such
    day (no calendar gap)."""
    fajr_dates = {e.entry_date for e in entries if e.fajr and e.entry_date <= today}
    if not fajr_dates:
        return 0
    cursor = max(fajr_dates)
    count = 0
    while cursor in fajr_dates:
        count += 1
        cursor -= timedelta(days=1)
    return count


def _to_badge_response(badge: UserBadge) -> BadgeResponse:
    return BadgeResponse(
        badge_id=badge.badge_id,
        badge_type=badge.badge_type,
        tier=badge.tier,
        earned_at=badge.earned_at,
    )


def _to_journal_response(entry: UserJournalEntry) -> JournalEntryResponse:
    quran = (
        QuranProgress(amount=entry.quran_amount, unit=entry.quran_unit)
        if entry.quran_amount is not None and entry.quran_unit is not None
        else None
    )
    return JournalEntryResponse(
        journal_id=entry.journal_id,
        entry_date=entry.entry_date,
        prayers=PrayerSet.model_validate(entry, from_attributes=True),
        quran=quran,
        is_protected=entry.is_protected,
        notes=entry.notes,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )
