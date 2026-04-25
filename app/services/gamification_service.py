import uuid
from datetime import date, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.models.enums import MasjidStatus
from app.models.user_badge import UserBadge
from app.models.user_journal_entry import UserJournalEntry
from app.repositories.masjid_repository import MasjidRepository
from app.repositories.user_badge_repository import UserBadgeRepository
from app.repositories.user_checkin_repository import UserCheckinRepository
from app.repositories.user_journal_repository import UserJournalRepository
from app.schemas.gamification import (
    BadgeResponse,
    CheckInCreate,
    CheckInHistoryItem,
    CheckInHistoryResponse,
    CheckInResponse,
    JournalEntryCreate,
    JournalEntryResponse,
    JournalListResponse,
    StreakResponse,
)


class GamificationService:
    def __init__(self, db: AsyncSession) -> None:
        self.checkin_repo = UserCheckinRepository(db)
        self.badge_repo = UserBadgeRepository(db)
        self.journal_repo = UserJournalRepository(db)
        self.masjid_repo = MasjidRepository(db)

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
            total=total,
            page=page,
            page_size=page_size,
        )

    async def list_badges(self, user: CurrentUser) -> list[BadgeResponse]:
        badges = await self.badge_repo.list_by_user(user.user_id)
        return [_to_badge_response(b) for b in badges]

    async def get_streak(self, user: CurrentUser) -> StreakResponse:
        streak = await self._compute_streak(user.user_id)
        total = await self.checkin_repo.count_by_user(user.user_id)
        return StreakResponse(current_streak=streak, total_checkins=total)

    async def list_journal(
        self,
        user: CurrentUser,
        page: int,
        page_size: int,
        date_from: date | None,
        date_to: date | None,
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
        data: JournalEntryCreate,
        user: CurrentUser,
    ) -> JournalEntryResponse:
        existing = await self.journal_repo.get_by_user_date(
            user.user_id, data.entry_date
        )
        if existing:
            fields = data.model_dump(exclude={"entry_date"})
            for k, v in fields.items():
                setattr(existing, k, v)
            await self.journal_repo.db.flush()
            await self.journal_repo.commit()
            return _to_journal_response(existing)

        entry = UserJournalEntry(user_id=user.user_id, **data.model_dump())
        await self.journal_repo.add(entry)
        await self.journal_repo.commit()
        return _to_journal_response(entry)

    async def _evaluate_badges(self, user_id: uuid.UUID) -> list[UserBadge]:
        awarded: list[UserBadge] = []
        total = await self.checkin_repo.count_by_user(user_id)
        streak = await self._compute_streak(user_id)

        if streak >= 7 and not await self.badge_repo.has_badge(user_id, "FajrWarrior"):
            awarded.append(await self.badge_repo.award(user_id, "FajrWarrior"))
        if total >= 25 and not await self.badge_repo.has_badge(
            user_id, "CommunityPillar"
        ):
            awarded.append(await self.badge_repo.award(user_id, "CommunityPillar"))
        return awarded

    async def _compute_streak(self, user_id: uuid.UUID) -> int:
        dates = await self.checkin_repo.get_distinct_dates(user_id)
        if not dates:
            return 0
        today = date.today()
        most_recent = dates[0]
        if (today - most_recent).days > 1:
            return 0
        streak = 0
        for i, d in enumerate(dates):
            if d == most_recent - timedelta(days=i):
                streak += 1
            else:
                break
        return streak


def _to_badge_response(badge: UserBadge) -> BadgeResponse:
    return BadgeResponse(
        badge_id=badge.badge_id,
        badge_type=badge.badge_type,
        earned_at=badge.earned_at,
    )


def _to_journal_response(entry: UserJournalEntry) -> JournalEntryResponse:
    return JournalEntryResponse(
        journal_id=entry.journal_id,
        entry_date=entry.entry_date,
        prayers_logged=entry.prayers_logged,
        quran_pages=entry.quran_pages,
        notes=entry.notes,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )
