"""Push fan-out service — minimal core of PRD 03's push subsystem.

This is the load-bearing slice the community PRD (07) rides for instant
announcement pushes and the daily digest. It owns:

- the device-token registry (register / prune),
- resolving a set of user_ids to their registered device tokens,
- dispatching a typed message to those devices through a pluggable transport.

The wire transport is selected by config: with ``PUSH_ENABLED`` false (dev/CI)
the default ``LoggingTransport`` records exactly what *would* be sent, keeping
the routing/bucketing logic real and testable without a provider; with it true
``ExpoPushTransport`` delivers for real. Because every caller constructs
``PushService(db)`` with no explicit transport, that single config switch reaches
all push-firing paths at once.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import PushMessageType
from app.repositories.device_token_repository import DeviceTokenRepository

if TYPE_CHECKING:
    from app.models.device_token import DeviceToken

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


@dataclass(frozen=True, slots=True)
class SendResult:
    """Outcome of one fan-out: how many devices accepted, plus any tokens the
    provider reported as permanently dead (``DeviceNotRegistered``) so the caller
    can reap them from the registry."""

    accepted: int
    invalid_tokens: tuple[str, ...] = ()


class PushTransport(Protocol):
    async def send(self, tokens: Sequence[str], message: PushMessage) -> SendResult:
        """Deliver ``message`` to ``tokens``; report accepted count + dead tokens."""
        ...


class LoggingTransport:
    """No-op transport used when ``PUSH_ENABLED`` is false. Logs the intended
    fan-out so behaviour is observable end-to-end without a real provider."""

    async def send(self, tokens: Sequence[str], message: PushMessage) -> SendResult:
        if not tokens:
            return SendResult(accepted=0)
        logger.info(
            "PUSH %s → %d device(s): %s | data=%s",
            message.message_type.value,
            len(tokens),
            message.title,
            message.data,
        )
        return SendResult(accepted=len(tokens))


def _build_default_transport() -> PushTransport:
    """Select the transport from config. ``ExpoPushTransport`` is imported lazily
    so the ``expo_push_transport → push_service`` import does not cycle."""
    if settings.PUSH_ENABLED:
        from app.services.expo_push_transport import ExpoPushTransport

        return ExpoPushTransport(access_token=settings.expo_access_token or None)
    return LoggingTransport()


class PushService:
    def __init__(
        self, db: AsyncSession, transport: PushTransport | None = None
    ) -> None:
        self.repo = DeviceTokenRepository(db)
        self.transport: PushTransport = transport or _build_default_transport()

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
        return await self._dispatch(tokens, message)

    async def notify_all(self, message: PushMessage) -> int:
        """Broadcast to every registered device — platform-wide announcements
        (PLATFORM_PUSH) and the Hijri-offset change ping (HIJRI_OFFSET). Same
        best-effort contract as ``notify_users``: never raises."""
        tokens = await self.repo.list_all_tokens()
        return await self._dispatch(tokens, message)

    async def broadcast_platform_push(
        self, title: str, body: str, data: dict | None = None
    ) -> int:
        """Build a ``PLATFORM_PUSH`` message and broadcast it to all devices.
        Thin wrapper so the admin route stays HTTP-only."""
        return await self.notify_all(
            PushMessage(
                message_type=PushMessageType.PLATFORM_PUSH,
                title=title,
                body=body,
                data=data or {},
            )
        )

    async def _dispatch(
        self, tokens: Sequence["DeviceToken"], message: PushMessage
    ) -> int:
        """Send ``message`` to already-resolved device tokens and reap any the
        provider reports dead. Shared by ``notify_users`` and ``notify_all`` —
        never raises (push is best-effort)."""
        if not tokens:
            return 0
        try:
            result = await self.transport.send([t.token for t in tokens], message)
        except Exception:
            logger.exception(
                "Push dispatch failed for %d device(s), type=%s",
                len(tokens),
                message.message_type.value,
            )
            return 0
        if result.invalid_tokens:
            # Reap permanently-dead tokens (e.g. app uninstall, which never hits
            # the logout prune path). Best-effort — never let registry hygiene
            # break the fan-out's caller.
            try:
                await self.repo.delete_tokens(result.invalid_tokens)
                await self.repo.commit()
            except Exception:
                logger.exception(
                    "Failed to prune %d dead device token(s)",
                    len(result.invalid_tokens),
                )
        return result.accepted
