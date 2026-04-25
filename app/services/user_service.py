import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import CurrentUser
from app.repositories.masjid_repository import MasjidRepository
from app.repositories.user_masjid_follow_repository import UserMasjidFollowRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.schemas.user import (
    FavouriteMasjidResponse,
    UserDataExport,
    UserProfileResponse,
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

    def _to_response(self, profile, email: str | None) -> UserProfileResponse:
        return UserProfileResponse(
            user_id=profile.user_id,
            email=email,
            display_name=profile.display_name,
            madhab=profile.madhab,
            profile_photo_url=profile.profile_photo_url,
            is_deleted=profile.is_deleted,
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
        rows = await self.follow_repo.list_masjids_for_user(user.user_id)
        export = UserDataExport(
            exported_at=datetime.now(timezone.utc),
            user_id=profile.user_id,
            email=user.email,
            display_name=profile.display_name,
            madhab=profile.madhab,
            profile_photo_url=profile.profile_photo_url,
            created_at=profile.created_at,
            followed_masjids=[self._to_favourite(m, fa) for m, fa in rows],
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
