import uuid

from sqlalchemy import delete, select, update

from app.models.masjid import MasjidPhoto
from app.repositories.base import BaseRepository


class MasjidPhotoRepository(BaseRepository[MasjidPhoto]):
    model = MasjidPhoto

    async def list_by_masjid(self, masjid_id: uuid.UUID) -> list[MasjidPhoto]:
        result = await self.db.execute(
            select(MasjidPhoto)
            .where(MasjidPhoto.masjid_id == masjid_id)
            .order_by(MasjidPhoto.display_order)
        )
        return list(result.scalars().all())

    async def get_by_id(self, photo_id: uuid.UUID) -> MasjidPhoto | None:
        result = await self.db.execute(
            select(MasjidPhoto).where(MasjidPhoto.photo_id == photo_id)
        )
        return result.scalar_one_or_none()

    async def count_by_masjid(self, masjid_id: uuid.UUID) -> int:
        from sqlalchemy import func

        result = await self.db.execute(
            select(func.count()).where(MasjidPhoto.masjid_id == masjid_id)
        )
        return result.scalar_one()

    async def create(
        self,
        *,
        masjid_id: uuid.UUID,
        url: str,
        is_cover: bool,
        display_order: int,
    ) -> MasjidPhoto:
        photo = MasjidPhoto(
            masjid_id=masjid_id,
            url=url,
            is_cover=is_cover,
            display_order=display_order,
        )
        self.db.add(photo)
        await self.db.flush()
        return photo

    async def set_cover(self, masjid_id: uuid.UUID, photo_id: uuid.UUID) -> None:
        await self.db.execute(
            update(MasjidPhoto)
            .where(MasjidPhoto.masjid_id == masjid_id)
            .values(is_cover=False)
        )
        await self.db.execute(
            update(MasjidPhoto)
            .where(MasjidPhoto.photo_id == photo_id)
            .values(is_cover=True)
        )
        await self.db.flush()

    async def delete_photo(self, photo: MasjidPhoto) -> None:
        await self.db.execute(
            delete(MasjidPhoto).where(MasjidPhoto.photo_id == photo.photo_id)
        )
        await self.db.flush()

    async def reorder(
        self, masjid_id: uuid.UUID, ordered_photo_ids: list[uuid.UUID]
    ) -> None:
        for i, photo_id in enumerate(ordered_photo_ids):
            await self.db.execute(
                update(MasjidPhoto)
                .where(
                    MasjidPhoto.photo_id == photo_id,
                    MasjidPhoto.masjid_id == masjid_id,
                )
                .values(display_order=i)
            )
        await self.db.flush()
