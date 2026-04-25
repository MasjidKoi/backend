from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _pk_name(self) -> str:
        return inspect(self.model).mapper.primary_key[0].name

    async def get_by_id(self, pk: UUID) -> ModelT | None:
        result = await self.db.execute(
            select(self.model).where(getattr(self.model, self._pk_name()) == pk)
        )
        return result.scalar_one_or_none()

    async def add(self, instance: ModelT) -> ModelT:
        """Add to session and flush (materialises server defaults, no commit)."""
        self.db.add(instance)
        await self.db.flush()
        return instance

    async def commit(self) -> None:
        """Explicit commit — called by service layer only, never by repositories."""
        await self.db.commit()

    async def refresh(self, instance: ModelT) -> ModelT:
        await self.db.refresh(instance)
        return instance
