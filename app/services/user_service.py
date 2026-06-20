import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import CurrentUser
from app.repositories.device_token_repository import DeviceTokenRepository
from app.repositories.donation_repository import DonationRepository
from app.repositories.masjid_event_repository import MasjidEventRepository
from app.repositories.masjid_question_repository import MasjidQuestionRepository
from app.repositories.masjid_report_repository import MasjidReportRepository
from app.repositories.masjid_repository import MasjidRepository
from app.repositories.masjid_review_repository import MasjidReviewRepository
from app.repositories.masjid_submission_repository import MasjidSubmissionRepository
from app.repositories.recurring_schedule_repository import RecurringScheduleRepository
from app.repositories.support_ticket_repository import SupportTicketRepository
from app.repositories.user_badge_repository import UserBadgeRepository
from app.repositories.user_checkin_repository import UserCheckinRepository
from app.repositories.user_goal_repository import UserGoalRepository
from app.repositories.user_journal_repository import UserJournalRepository
from app.repositories.user_masjid_follow_repository import UserMasjidFollowRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.schemas.user import (
    FavouriteMasjidResponse,
    UserProfileResponse,
)
from app.schemas.user_export import (
    BadgeExport,
    CheckinExport,
    DeviceTokenExport,
    DonationExport,
    EventRsvpExport,
    GoalExport,
    JournalEntryExport,
    QuestionExport,
    RecurringScheduleExport,
    ReportExport,
    ReviewExport,
    SubmissionExport,
    SupportTicketExport,
    UserDataExport,
)
from app.services.email_service import send_email
from app.services.storage import StorageService

AVATAR_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
AVATAR_MAX_BYTES = 2 * 1024 * 1024  # 2 MB


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = UserProfileRepository(db)
        self.follow_repo = UserMasjidFollowRepository(db)
        self.masjid_repo = MasjidRepository(db)
        # Repositories aggregated by the full data export (PRD 09 §portability).
        self.donation_repo = DonationRepository(db)
        self.review_repo = MasjidReviewRepository(db)
        self.question_repo = MasjidQuestionRepository(db)
        self.submission_repo = MasjidSubmissionRepository(db)
        self.checkin_repo = UserCheckinRepository(db)
        self.journal_repo = UserJournalRepository(db)
        self.goal_repo = UserGoalRepository(db)
        self.badge_repo = UserBadgeRepository(db)
        self.ticket_repo = SupportTicketRepository(db)
        self.device_repo = DeviceTokenRepository(db)
        self.report_repo = MasjidReportRepository(db)
        self.recurring_repo = RecurringScheduleRepository(db)
        self.event_repo = MasjidEventRepository(db)

    def _to_response(self, profile, email: str | None) -> UserProfileResponse:
        return UserProfileResponse(
            user_id=profile.user_id,
            email=email,
            display_name=profile.display_name,
            madhab=profile.madhab,
            profile_photo_url=profile.profile_photo_url,
            is_deleted=profile.is_deleted,
            donate_anonymously_by_default=profile.donate_anonymously_by_default,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )

    def _to_favourite(self, masjid, followed_at: datetime) -> FavouriteMasjidResponse:
        return FavouriteMasjidResponse(
            masjid_id=masjid.masjid_id,
            name=masjid.name,
            address=masjid.address,
            admin_region=masjid.admin_region,
            verified=masjid.verified,
            followed_at=followed_at,
        )

    async def get_me(self, user: CurrentUser) -> UserProfileResponse:
        profile = await self.repo.get_or_create(user.user_id, user.email)
        if profile.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Account has been deleted",
            )
        await self.repo.commit()
        return self._to_response(profile, user.email)

    async def update_me(
        self,
        user: CurrentUser,
        display_name: str | None,
        madhab: str | None,
        photo: UploadFile | None,
        storage: StorageService,
    ) -> UserProfileResponse:
        profile = await self.repo.get_or_create(user.user_id, user.email)
        if profile.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Account has been deleted",
            )

        fields: dict = {}
        if display_name is not None:
            fields["display_name"] = display_name
        if madhab is not None:
            fields["madhab"] = madhab

        if photo is not None:
            content_type = photo.content_type or ""
            if content_type not in AVATAR_ALLOWED_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail=f"Unsupported type: {content_type}. Use JPEG, PNG, or WebP.",
                )
            data = await photo.read(AVATAR_MAX_BYTES + 1)
            if len(data) > AVATAR_MAX_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Avatar must be ≤ 2 MB",
                )
            ext = content_type.split("/")[-1].replace("jpeg", "jpg")
            key = f"avatars/{user.user_id}/{uuid.uuid4()}.{ext}"
            await storage.upload(
                bucket=settings.S3_BUCKET_AVATARS,
                key=key,
                data=data,
                content_type=content_type,
            )
            # Delete old avatar from storage
            if profile.profile_photo_url:
                old_prefix = f"{settings.s3_endpoint}/{settings.S3_BUCKET_AVATARS}/"
                old_key = profile.profile_photo_url.removeprefix(old_prefix)
                if old_key != profile.profile_photo_url:
                    await storage.delete(settings.S3_BUCKET_AVATARS, old_key)
            fields["profile_photo_url"] = (
                f"{settings.s3_endpoint}/{settings.S3_BUCKET_AVATARS}/{key}"
            )

        if fields:
            await self.repo.update(profile, fields)

        await self.repo.commit()
        # Reload — onupdate=func.now() expires updated_at after flush
        profile = await self.repo.get_by_user_id(user.user_id)
        return self._to_response(profile, user.email)

    async def delete_me(self, user: CurrentUser) -> None:
        profile = await self.repo.get_or_create(user.user_id, user.email)
        if profile.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Account is already pending deletion",
            )
        await self.repo.soft_delete(profile)
        await self.repo.commit()
        if user.email:
            await send_email(
                to=user.email,
                subject="MasjidKoi — Account deletion initiated",
                body=(
                    "Your account deletion request has been received. "
                    "Your data will be permanently purged within 30 days. "
                    "If this was a mistake, please contact support immediately."
                ),
            )

    async def export_me(self, user: CurrentUser) -> bytes:
        profile = await self.repo.get_or_create(user.user_id, user.email)
        if profile.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Account has been deleted",
            )
        await self.repo.commit()
        uid = user.user_id

        # All repos share this one request session, so these fetches MUST be
        # sequential awaits — NOT asyncio.gather (async sessions are not
        # concurrency-safe; CLAUDE.md §5).
        follows = await self.follow_repo.list_masjids_for_user(uid)
        donations = await self.donation_repo.list_all_for_user(uid)
        reviews = await self.review_repo.list_all_for_user(uid)
        questions = await self.question_repo.list_for_user(uid)
        submissions = await self.submission_repo.list_for_user(uid)
        checkins = await self.checkin_repo.list_all_for_user(uid)
        journal = await self.journal_repo.get_day_records(uid)
        goals = await self.goal_repo.list_for_user(uid)
        badges = await self.badge_repo.list_by_user(uid)
        tickets = await self.ticket_repo.list_for_user(uid)
        devices = await self.device_repo.list_for_user(uid)
        reports = await self.report_repo.list_for_user(uid)
        recurring = await self.recurring_repo.list_by_user(uid)
        rsvps = await self.event_repo.list_rsvps_for_user(uid)

        # Goals carry their completion dates; the ORM `completions` relationship
        # is lazy="raise", so fetch the dates explicitly per goal.
        goal_exports: list[GoalExport] = []
        for g in goals:
            ge = GoalExport.model_validate(g)
            ge.completion_dates = sorted(
                await self.goal_repo.completion_dates(g.goal_id)
            )
            goal_exports.append(ge)

        export = UserDataExport(
            exported_at=datetime.now(timezone.utc),
            user_id=profile.user_id,
            email=user.email,
            display_name=profile.display_name,
            madhab=profile.madhab,
            profile_photo_url=profile.profile_photo_url,
            created_at=profile.created_at,
            followed_masjids=[self._to_favourite(m, fa) for m, fa in follows],
            donations=[DonationExport.model_validate(d) for d in donations],
            reviews=[ReviewExport.model_validate(r) for r in reviews],
            questions=[QuestionExport.model_validate(q) for q in questions],
            submissions=[SubmissionExport.model_validate(s) for s in submissions],
            checkins=[CheckinExport.model_validate(c) for c in checkins],
            journal_entries=[JournalEntryExport.model_validate(j) for j in journal],
            goals=goal_exports,
            badges=[BadgeExport.model_validate(b) for b in badges],
            support_tickets=[SupportTicketExport.model_validate(t) for t in tickets],
            device_tokens=[DeviceTokenExport.model_validate(d) for d in devices],
            reports=[ReportExport.model_validate(r) for r in reports],
            recurring_schedules=[
                RecurringScheduleExport.model_validate(s) for s in recurring
            ],
            event_rsvps=[EventRsvpExport.model_validate(r) for r in rsvps],
        )
        return export.model_dump_json(indent=2).encode()

    async def list_favourites(self, user: CurrentUser) -> list[FavouriteMasjidResponse]:
        rows = await self.follow_repo.list_masjids_for_user(user.user_id)
        return [self._to_favourite(m, fa) for m, fa in rows]

    async def add_favourite(self, user: CurrentUser, masjid_id: uuid.UUID) -> None:
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Masjid not found",
            )
        await self.follow_repo.follow(user.user_id, masjid_id)
        await self.follow_repo.commit()

    async def remove_favourite(self, user: CurrentUser, masjid_id: uuid.UUID) -> None:
        await self.follow_repo.unfollow(user.user_id, masjid_id)
        await self.follow_repo.commit()
