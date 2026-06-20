import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.models.device_token import DeviceToken
from app.repositories.base import BaseRepository


class DeviceTokenRepository(BaseRepository[DeviceToken]):
    model = DeviceToken

    async def upsert(
        self, token: str, user_id: uuid.UUID, platform: str
    ) -> DeviceToken:
        """Idempotent per token: re-registering rotates owner/platform/last_seen
        rather than creating a duplicate row (token is unique)."""
        existing = await self.get_by_token(token)
        now = datetime.now(timezone.utc)
        if existing is not None:
            existing.user_id = user_id
            existing.platform = platform
            existing.last_seen_at = now
            await self.db.flush()
            return existing
        row = DeviceToken(token=token, user_id=user_id, platform=platform)
        self.db.add(row)
        await self.db.flush()
        return row

    async def get_by_token(self, token: str) -> DeviceToken | None:
        result = await self.db.execute(
            select(DeviceToken).where(DeviceToken.token == token)
        )
        return result.scalar_one_or_none()

    async def delete_by_token(self, token: str, user_id: uuid.UUID) -> None:
        """Prune on logout — scoped to the owning user so one user cannot drop
        another's token."""
        await self.db.execute(
            delete(DeviceToken).where(
                DeviceToken.token == token,
                DeviceToken.user_id == user_id,
            )
        )
        await self.db.flush()

    async def delete_tokens(self, tokens: Sequence[str]) -> int:
        """Bulk-prune dead tokens the push provider rejected as
        ``DeviceNotRegistered``. Owner-agnostic — such a token is globally dead,
        regardless of which user it was last registered to."""
        if not tokens:
            return 0
        result = await self.db.execute(
            delete(DeviceToken).where(DeviceToken.token.in_(list(tokens)))
        )
        await self.db.flush()
        return result.rowcount or 0

    async def list_tokens_for_users(
        self, user_ids: Sequence[uuid.UUID]
    ) -> list[DeviceToken]:
        if not user_ids:
            return []
        result = await self.db.execute(
            select(DeviceToken).where(DeviceToken.user_id.in_(list(user_ids)))
        )
        return list(result.scalars().all())

    async def list_all_tokens(self) -> list[DeviceToken]:
        """Every registered device token — backs the platform-wide broadcast
        (PLATFORM_PUSH / HIJRI_OFFSET). v1 loads all rows into memory; token
        pagination / async queueing is a future scale follow-up."""
        result = await self.db.execute(select(DeviceToken))
        return list(result.scalars().all())
