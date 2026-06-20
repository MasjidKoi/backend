import logging
import uuid

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import CurrentUser
from app.models.enums import MasjidSubmissionStatus, PushMessageType
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.masjid_repository import MasjidRepository
from app.repositories.masjid_submission_repository import MasjidSubmissionRepository
from app.schemas.masjid_submission import (
    MasjidSubmissionAdminResponse,
    MasjidSubmissionApprove,
    MasjidSubmissionCreate,
    MasjidSubmissionListResponse,
    MasjidSubmissionResponse,
    SubmissionPhotoUploadResponse,
)
from app.services.push_service import PushMessage, PushService
from app.services.storage import StorageService

logger = logging.getLogger(__name__)

# Max simultaneously-pending submissions per user (abuse guard, PRD 02 ~3).
_MAX_PENDING_PER_USER = 3

# Photo upload limits — mirror the community-photo pipeline (kept local so this
# path stays independent of that service).
MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
_EXT_MAP = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class MasjidSubmissionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = MasjidSubmissionRepository(db)
        self.masjid_repo = MasjidRepository(db)  # same session — atomic on approve
        self.audit = AuditLogRepository(db)

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _get_or_404(self, submission_id: uuid.UUID):
        submission = await self.repo.get_by_id(submission_id)
        if not submission:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Submission not found",
            )
        return submission

    # ── Consumer ─────────────────────────────────────────────────────────────

    async def create(
        self, data: MasjidSubmissionCreate, user: CurrentUser
    ) -> MasjidSubmissionResponse:
        pending = await self.repo.count_pending_for_user(user.user_id)
        if pending >= _MAX_PENDING_PER_USER:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"You already have {pending} pending submissions "
                    f"(max {_MAX_PENDING_PER_USER}). Wait for review before "
                    "submitting more."
                ),
            )

        submission = await self.repo.create(
            user_id=user.user_id,
            name=data.name.strip(),
            latitude=data.latitude,
            longitude=data.longitude,
            address=data.address.strip() if data.address else None,
            photo_key=data.photo_key,
        )
        await self.repo.commit()
        logger.info(
            "Masjid submission created",
            extra={
                "submission_id": str(submission.submission_id),
                "user_id": str(user.user_id),
            },
        )
        return MasjidSubmissionResponse.model_validate(submission)

    async def list_mine(self, user: CurrentUser) -> list[MasjidSubmissionResponse]:
        rows = await self.repo.list_for_user(user.user_id)
        return [MasjidSubmissionResponse.model_validate(r) for r in rows]

    async def upload_photo(
        self, file: UploadFile, user: CurrentUser, storage: StorageService
    ) -> SubmissionPhotoUploadResponse:
        """Store a submission photo and return its key — the client attaches the
        key to the subsequent create-submission body (PRD 02). Validation mirrors
        the community-photo pipeline; 415/413/422 are intentionally distinct."""
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

        ext = _EXT_MAP.get(content_type, "jpg")
        key = f"submissions/{user.user_id}/{uuid.uuid4()}.{ext}"
        await storage.upload(
            bucket=settings.S3_BUCKET_PHOTOS,
            key=key,
            data=raw,
            content_type=content_type,
        )
        url = f"{settings.s3_endpoint}/{settings.S3_BUCKET_PHOTOS}/{key}"
        logger.info(
            "Submission photo uploaded",
            extra={"user_id": str(user.user_id), "key": key},
        )
        return SubmissionPhotoUploadResponse(photo_key=key, url=url)

    # ── Admin ────────────────────────────────────────────────────────────────

    async def list_for_admin(
        self,
        *,
        status_filter: str | None,
        page: int,
        page_size: int,
    ) -> MasjidSubmissionListResponse:
        rows, total = await self.repo.list_for_admin(
            status=status_filter,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return MasjidSubmissionListResponse(
            items=[MasjidSubmissionAdminResponse.model_validate(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def approve(
        self,
        submission_id: uuid.UUID,
        data: MasjidSubmissionApprove,
        user: CurrentUser,
    ) -> MasjidSubmissionAdminResponse:
        submission = await self._get_or_404(submission_id)
        if submission.status != MasjidSubmissionStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Submission already {submission.status}",
            )

        # A real masjid requires an address; fall back to the submitted one.
        address = (data.address or submission.address or "").strip()
        if not address:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "address is required to create the masjid — "
                    "provide one in the approval body"
                ),
            )
        name = (data.name or submission.name).strip()

        # Create the live masjid through the normal create path (status defaults
        # to pending — the platform admin activates it via the masjid lifecycle).
        masjid = await self.masjid_repo.create(
            name=name,
            address=address,
            admin_region=data.admin_region.strip(),
            lat=submission.latitude,
            lng=submission.longitude,
        )
        await self.repo.set_status(
            submission,
            MasjidSubmissionStatus.APPROVED,
            approved_masjid_id=masjid.masjid_id,
        )
        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="approve_masjid_submission",
            target_entity="masjid_submission",
            target_id=submission_id,
            details={"approved_masjid_id": str(masjid.masjid_id)},
        )
        await self.repo.commit()
        # onupdate=func.now() expired updated_at server-side; reload before serialising.
        await self.repo.refresh(submission)
        # Notify the submitter their masjid is live (best-effort — a push failure
        # must never undo the approval; delivers for real once a transport lands).
        await PushService(self.db).notify_users(
            [submission.user_id],
            PushMessage(
                message_type=PushMessageType.SUBMISSION_APPROVED,
                title="Your masjid is now live",
                body=f"{name} has been approved and added to MasjidKoi.",
                data={
                    "submission_id": str(submission_id),
                    "masjid_id": str(masjid.masjid_id),
                },
            ),
        )
        logger.info(
            "Masjid submission approved",
            extra={
                "submission_id": str(submission_id),
                "masjid_id": str(masjid.masjid_id),
            },
        )
        return MasjidSubmissionAdminResponse.model_validate(submission)

    async def reject(
        self, submission_id: uuid.UUID, user: CurrentUser
    ) -> MasjidSubmissionAdminResponse:
        submission = await self._get_or_404(submission_id)
        if submission.status != MasjidSubmissionStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Submission already {submission.status}",
            )
        await self.repo.set_status(submission, MasjidSubmissionStatus.REJECTED)
        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="reject_masjid_submission",
            target_entity="masjid_submission",
            target_id=submission_id,
        )
        await self.repo.commit()
        # onupdate=func.now() expired updated_at server-side; reload before serialising.
        await self.repo.refresh(submission)
        return MasjidSubmissionAdminResponse.model_validate(submission)
