import logging
import uuid as uuid_lib
import uuid

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import CurrentUser
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.masjid_photo_repository import MasjidPhotoRepository
from app.repositories.masjid_repository import MasjidRepository
from app.schemas.masjid import PhotoResponse
from app.services.storage import StorageService

logger = logging.getLogger(__name__)

MAX_PHOTOS = 10
MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
_EXT_MAP = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class MasjidPhotoService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = MasjidPhotoRepository(db)
        self.masjid_repo = MasjidRepository(db)
        self.audit = AuditLogRepository(db)

    def _check_scope(self, user: CurrentUser, masjid_id: uuid.UUID) -> None:
        if user.is_platform_admin:
            return
        if user.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to your own masjid",
            )

    async def upload(
        self,
        masjid_id: uuid.UUID,
        file: UploadFile,
        user: CurrentUser,
        storage: StorageService,
    ) -> PhotoResponse:
        self._check_scope(user, masjid_id)

        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Masjid not found"
            )

        count = await self.repo.count_by_masjid(masjid_id)
        if count >= MAX_PHOTOS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Maximum {MAX_PHOTOS} photos per masjid",
            )

        content_type = file.content_type or ""
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported image type. Allowed: jpeg, png, webp",
            )

        raw = await file.read(MAX_PHOTO_BYTES + 1)
        if len(raw) > MAX_PHOTO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Photo exceeds 5 MB limit",
            )

        ext = _EXT_MAP.get(content_type, "jpg")
        key = f"photos/{masjid_id}/{uuid_lib.uuid4()}.{ext}"
        await storage.upload(
            bucket=settings.S3_BUCKET_PHOTOS,
            key=key,
            data=raw,
            content_type=content_type,
        )

        url = f"{settings.s3_endpoint}/{settings.S3_BUCKET_PHOTOS}/{key}"
        is_cover = count == 0
        photo = await self.repo.create(
            masjid_id=masjid_id,
            url=url,
            is_cover=is_cover,
            display_order=count,
        )

        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="upload_photo",
            target_entity="masjid",
            target_id=masjid_id,
            details={"photo_id": str(photo.photo_id), "key": key},
        )
        await self.repo.commit()
        return PhotoResponse.model_validate(photo, from_attributes=True)

    async def set_cover(
        self, masjid_id: uuid.UUID, photo_id: uuid.UUID, user: CurrentUser
    ) -> list[PhotoResponse]:
        self._check_scope(user, masjid_id)
        photo = await self.repo.get_by_id(photo_id)
        if not photo or photo.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found"
            )
        await self.repo.set_cover(masjid_id, photo_id)
        await self.repo.commit()
        photos = await self.repo.list_by_masjid(masjid_id)
        return [PhotoResponse.model_validate(p, from_attributes=True) for p in photos]

    async def reorder(
        self, masjid_id: uuid.UUID, ordered_ids: list[uuid.UUID], user: CurrentUser
    ) -> list[PhotoResponse]:
        self._check_scope(user, masjid_id)
        await self.repo.reorder(masjid_id, ordered_ids)
        await self.repo.commit()
        photos = await self.repo.list_by_masjid(masjid_id)
        return [PhotoResponse.model_validate(p, from_attributes=True) for p in photos]

    async def delete_photo(
        self,
        masjid_id: uuid.UUID,
        photo_id: uuid.UUID,
        user: CurrentUser,
        storage: StorageService,
    ) -> None:
        self._check_scope(user, masjid_id)
        photo = await self.repo.get_by_id(photo_id)
        if not photo or photo.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found"
            )

        # Extract key from URL for deletion
        prefix = f"{settings.s3_endpoint}/{settings.S3_BUCKET_PHOTOS}/"
        key = photo.url[len(prefix) :] if photo.url.startswith(prefix) else photo.url

        await storage.delete(bucket=settings.S3_BUCKET_PHOTOS, key=key)
        await self.repo.delete_photo(photo)

        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="delete_photo",
            target_entity="masjid",
            target_id=masjid_id,
            details={"photo_id": str(photo_id)},
        )
        await self.repo.commit()
