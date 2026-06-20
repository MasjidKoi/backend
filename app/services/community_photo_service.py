import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import CurrentUser
from app.models.enums import PhotoModerationStatus, PhotoSource, PushMessageType
from app.models.masjid import MasjidPhoto
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.co_admin_invite_repository import CoAdminInviteRepository
from app.repositories.masjid_photo_repository import MasjidPhotoRepository
from app.repositories.masjid_repository import MasjidRepository
from app.schemas.community_photo import (
    CommunityPhotoModerationListResponse,
    CommunityPhotoModerationResponse,
    CommunityPhotoPublic,
    CommunityPhotoPublicListResponse,
    CommunityPhotoSubmissionResponse,
)
from app.services.gamification_service import GamificationService
from app.services.moderation_routing import can_moderate, ngo_pending_cutoff
from app.services.push_service import PushMessage, PushService
from app.services.storage import StorageService

logger = logging.getLogger(__name__)

MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
_EXT_MAP = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

# Abuse guards enforced in the DB (deterministic, restart-safe) — distinct from
# the coarse per-IP Redis limiter on the route. A 429 here is distinguishable
# from the 4xx validation failures below by both status and detail.
_MAX_PER_USER_PER_DAY = 10
_MAX_PER_USER_PER_MASJID_PER_DAY = 3
_WINDOW = timedelta(hours=24)


class CommunityPhotoService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = MasjidPhotoRepository(db)
        self.masjid_repo = MasjidRepository(db)
        self.co_admin = CoAdminInviteRepository(db)
        self.audit = AuditLogRepository(db)

    # ── Internal: moderation routing (Gap #10, shared with masjid Q&A) ───────

    async def _ngo_cutoff_for_list(
        self, user: CurrentUser, masjid_id: uuid.UUID
    ) -> datetime | None:
        """Authorise the caller for this masjid's moderation queue and return the
        NGO overdue-cutoff to apply (None = unrestricted full queue)."""
        if user.is_platform_admin:
            # Claimed masjid → the NGO sees only the overdue/resolved slice.
            # Unclaimed → the NGO owns the whole queue.
            if await self.co_admin.masjid_has_claimed_admin(masjid_id):
                return ngo_pending_cutoff(datetime.now(timezone.utc))
            return None
        if user.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to your own masjid",
            )
        return None

    async def _authorize_action(self, user: CurrentUser, photo: MasjidPhoto) -> None:
        """Authorise a moderation action on one item via the shared predicate."""
        has_claimed = await self.co_admin.masjid_has_claimed_admin(photo.masjid_id)
        if not can_moderate(
            is_platform_admin=user.is_platform_admin,
            user_masjid_id=user.masjid_id,
            item_masjid_id=photo.masjid_id,
            masjid_has_claimed_admin=has_claimed,
            pending_since=photo.created_at,
            now=datetime.now(timezone.utc),
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to moderate this photo",
            )

    async def _get_community_or_404(self, photo_id: uuid.UUID) -> MasjidPhoto:
        photo = await self.repo.get_by_id(photo_id)
        if not photo or photo.source != PhotoSource.COMMUNITY:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Community photo not found",
            )
        return photo

    # ── Consumer upload ──────────────────────────────────────────────────────

    async def submit(
        self,
        masjid_id: uuid.UUID,
        file: UploadFile,
        user: CurrentUser,
        storage: StorageService,
    ) -> CommunityPhotoSubmissionResponse:
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Masjid not found"
            )

        # Validation failures (415/413) are intentionally distinct from the 429
        # rate-limit responses below so clients can tell them apart.
        content_type = file.content_type or ""
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Unsupported image type. Allowed: jpeg, png, webp",
            )
        raw = await file.read(MAX_PHOTO_BYTES + 1)
        if len(raw) > MAX_PHOTO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Photo exceeds 5 MB limit",
            )
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Empty file",
            )

        since = datetime.now(timezone.utc) - _WINDOW
        per_masjid = await self.repo.count_community_by_user_masjid_since(
            user.user_id, masjid_id, since
        )
        if per_masjid >= _MAX_PER_USER_PER_MASJID_PER_DAY:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Submission limit reached — max "
                    f"{_MAX_PER_USER_PER_MASJID_PER_DAY} photos per masjid per day"
                ),
            )
        per_user = await self.repo.count_community_by_user_since(user.user_id, since)
        if per_user >= _MAX_PER_USER_PER_DAY:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Submission limit reached — max {_MAX_PER_USER_PER_DAY} "
                    "photos per day"
                ),
            )

        ext = _EXT_MAP.get(content_type, "jpg")
        key = f"community/{masjid_id}/{uuid.uuid4()}.{ext}"
        await storage.upload(
            bucket=settings.S3_BUCKET_PHOTOS,
            key=key,
            data=raw,
            content_type=content_type,
        )
        url = f"{settings.s3_endpoint}/{settings.S3_BUCKET_PHOTOS}/{key}"

        photo = await self.repo.create(
            masjid_id=masjid_id,
            url=url,
            is_cover=False,
            display_order=0,
            source=PhotoSource.COMMUNITY,
            status=PhotoModerationStatus.PENDING,
            uploaded_by=user.user_id,
        )
        await self.repo.commit()
        await self.repo.refresh(photo)
        logger.info(
            "Community photo submitted",
            extra={
                "photo_id": str(photo.photo_id),
                "masjid_id": str(masjid_id),
                "user_id": str(user.user_id),
            },
        )
        return CommunityPhotoSubmissionResponse.model_validate(photo)

    # ── Public listing ─────────────────────────────────────────────────────────

    async def list_public(
        self, masjid_id: uuid.UUID, *, page: int, page_size: int
    ) -> CommunityPhotoPublicListResponse:
        rows, total = await self.repo.list_approved_community(
            masjid_id, offset=(page - 1) * page_size, limit=page_size
        )
        return CommunityPhotoPublicListResponse(
            items=[CommunityPhotoPublic.model_validate(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    # ── Submitter ────────────────────────────────────────────────────────────

    async def list_mine(
        self, user: CurrentUser
    ) -> list[CommunityPhotoSubmissionResponse]:
        rows = await self.repo.list_community_for_user(user.user_id)
        return [CommunityPhotoSubmissionResponse.model_validate(r) for r in rows]

    # ── Moderation ─────────────────────────────────────────────────────────────

    async def list_for_moderation(
        self,
        masjid_id: uuid.UUID,
        user: CurrentUser,
        *,
        status_filter: str | None,
        page: int,
        page_size: int,
    ) -> CommunityPhotoModerationListResponse:
        ngo_overdue_before = await self._ngo_cutoff_for_list(user, masjid_id)
        rows, total = await self.repo.list_community_for_masjid_moderation(
            masjid_id,
            status=status_filter,
            offset=(page - 1) * page_size,
            limit=page_size,
            ngo_overdue_before=ngo_overdue_before,
        )
        return CommunityPhotoModerationListResponse(
            items=[CommunityPhotoModerationResponse.model_validate(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def approve(
        self, photo_id: uuid.UUID, user: CurrentUser
    ) -> CommunityPhotoModerationResponse:
        return await self._moderate(
            photo_id, user, PhotoModerationStatus.APPROVED, "approve_community_photo"
        )

    async def reject(
        self, photo_id: uuid.UUID, user: CurrentUser
    ) -> CommunityPhotoModerationResponse:
        return await self._moderate(
            photo_id, user, PhotoModerationStatus.REJECTED, "reject_community_photo"
        )

    async def _moderate(
        self,
        photo_id: uuid.UUID,
        user: CurrentUser,
        new_status: str,
        action: str,
    ) -> CommunityPhotoModerationResponse:
        photo = await self._get_community_or_404(photo_id)
        await self._authorize_action(user, photo)
        if photo.status != PhotoModerationStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Photo already {photo.status}",
            )
        await self.repo.set_status(photo, new_status)
        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action=action,
            target_entity="masjid_photo",
            target_id=photo_id,
            details={"masjid_id": str(photo.masjid_id)},
        )
        await self.repo.commit()
        await self.repo.refresh(photo)
        # On approval, notify the submitter (best-effort). Reject sends nothing.
        # uploaded_by is nullable (SET NULL on account deletion) — guard it.
        if new_status == PhotoModerationStatus.APPROVED and photo.uploaded_by:
            await PushService(self.db).notify_users(
                [photo.uploaded_by],
                PushMessage(
                    message_type=PushMessageType.PHOTO_APPROVED,
                    title="Your photo was approved",
                    body="A photo you submitted is now on the masjid's profile.",
                    data={
                        "masjid_id": str(photo.masjid_id),
                        "photo_id": str(photo.photo_id),
                    },
                ),
            )
            # An approved photo counts toward Community Pillar (PRD 08); re-evaluate
            # the uploader's badges. Best-effort, so a failure never breaks approval.
            try:
                await GamificationService(self.db).reevaluate_badges(photo.uploaded_by)
            except Exception:
                logger.exception(
                    "Badge re-eval failed after photo approval %s", photo_id
                )
                # Clear the poisoned session so later use doesn't hit
                # PendingRollbackError — matches the donation/report hooks.
                await self.db.rollback()
        logger.info("Community photo %s", new_status, extra={"photo_id": str(photo_id)})
        return CommunityPhotoModerationResponse.model_validate(photo)
