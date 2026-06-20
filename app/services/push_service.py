"""Push fan-out service — minimal core of PRD 03's push subsystem.

This is the load-bearing slice the community PRD (07) rides for instant
announcement pushes and the daily digest. It owns:

- the device-token registry (register / prune),
- resolving a set of user_ids to their registered device tokens,
- dispatching a typed message to those devices through a pluggable transport.

The actual wire transport (Expo Push / bare FCM) is deferred — Firebase/APNs
credentials don't exist in this environment yet (gap #6's open dependency). The
default ``LoggingTransport`` records exactly what *would* be sent, which keeps
the routing/bucketing logic (#15/#16) real and testable today. Swapping in a
real transport later is a one-line change in ``get_push_service``.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PushMessageType
from app.repositories.device_token_repository import DeviceTokenRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PushMessage:
    """A single typed push. ``message_type`` is the discriminator the mobile
    PushLink routes on; ``data`` carries the deep-link payload (masjid id, item
    id, etc.)."""

    message_type: PushMessageType
    title: str
    body: str
    data: dict = field(default_factory=dict)


class PushTransport(Protocol):
    async def send(self, tokens: Sequence[str], message: PushMessage) -> int:
        """Deliver ``message`` to ``tokens``; return the number accepted."""
        ...


class LoggingTransport:
    """No-op transport used until FCM/APNs credentials land. Logs the intended
    fan-out so behaviour is observable end-to-end without a real provider."""

    async def send(self, tokens: Sequence[str], message: PushMessage) -> int:
        if not tokens:
            return 0
        logger.info(
            "PUSH %s → %d device(s): %s | data=%s",
            message.message_type.value,
            len(tokens),
            message.title,
            message.data,
        )
        return len(tokens)


class PushService:
    def __init__(
        self, db: AsyncSession, transport: PushTransport | None = None
    ) -> None:
        self.repo = DeviceTokenRepository(db)
        self.transport: PushTransport = transport or LoggingTransport()

    # ── Token registry ──────────────────────────────────────────────────────

    async def register_device(
        self, user_id: uuid.UUID, token: str, platform: str
    ) -> None:
        await self.repo.upsert(token, user_id, platform)
        await self.repo.commit()

    async def prune_device(self, user_id: uuid.UUID, token: str) -> None:
        await self.repo.delete_by_token(token, user_id)
        await self.repo.commit()

    # ── Fan-out ───────────────────────────────────────────────────────────────

    async def notify_users(
        self, user_ids: Sequence[uuid.UUID], message: PushMessage
    ) -> int:
        """Resolve users to their devices and dispatch. Returns the device count
        sent to. Never raises on transport failure — push is best-effort and
        must not break the action that triggered it."""
        if not user_ids:
            return 0
        tokens = await self.repo.list_tokens_for_users(user_ids)
        if not tokens:
            return 0
        try:
            return await self.transport.send([t.token for t in tokens], message)
        except Exception:
            logger.exception(
                "Push dispatch failed for %d device(s), type=%s",
                len(tokens),
                message.message_type.value,
            )
            return 0
