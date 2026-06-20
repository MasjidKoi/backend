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
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import PushMessageType
from app.models.user_profile import UserProfile
from app.repositories.device_token_repository import DeviceTokenRepository
from app.repositories.push_receipt_repository import PushReceiptRepository

# Expo delivery receipts are ready a few minutes after a send; we poll those
# older than this so a token's async DeviceNotRegistered is caught and reaped.
RECEIPT_POLL_DELAY_MINUTES = 15
RECEIPT_POLL_BATCH = 1000

if TYPE_CHECKING:
    from app.models.device_token import DeviceToken

logger = logging.getLogger(__name__)

# Per-message-type push gating (PRD 05 #4 / PRD 09 #28). Maps each push type to
# the UserProfile boolean column that mutes it, or ``None`` for always-on
# (transactional / correctness) types that have no switch by design. Masjid-scoped
# types (instant announcement, digest, time-change) are gated upstream per-follow
# via notification_mode, so they are ``None`` here. Every PushMessageType MUST
# appear so the gate is exhaustive and auditable.
_MUTE_COLUMN_BY_TYPE: dict[PushMessageType, str | None] = {
    # Gateable
    PushMessageType.RECURRING_NUDGE: "mute_donation_nudge",
    PushMessageType.CAMPAIGN_MILESTONE: "mute_campaign_milestone",
    PushMessageType.SUBMISSION_APPROVED: "mute_moderation_outcome",
    PushMessageType.PHOTO_APPROVED: "mute_moderation_outcome",
    PushMessageType.QNA_ANSWERED: "mute_moderation_outcome",
    PushMessageType.PLATFORM_PUSH: "mute_promotions",
    # Always-on — transactional / correctness
    PushMessageType.DONATION_CONFIRMED: None,
    PushMessageType.PAYMENT_RECOVERY: None,
    PushMessageType.HIJRI_OFFSET: None,
    # Always-on here — already gated per-follow upstream
    PushMessageType.ANNOUNCEMENT_INSTANT: None,
    PushMessageType.DAILY_DIGEST: None,
    PushMessageType.TIME_CHANGE: None,
}

# Fail at import, not mid-fan-out, if the gate ever drifts: a type missing from
# the map would silently default to always-on (leaking pushes a user muted), and
# a misspelled column name would raise AttributeError out of the best-effort push
# path. Both become a loud startup error instead.
_unmapped_types = set(PushMessageType) - set(_MUTE_COLUMN_BY_TYPE)
if _unmapped_types:
    raise RuntimeError(
        f"PushMessageType(s) missing from _MUTE_COLUMN_BY_TYPE: {_unmapped_types}"
    )
_bad_mute_columns = [
    column
    for column in _MUTE_COLUMN_BY_TYPE.values()
    if column is not None and not hasattr(UserProfile, column)
]
if _bad_mute_columns:
    raise RuntimeError(f"Unknown UserProfile mute column(s): {_bad_mute_columns}")


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
    """Outcome of one fan-out: how many devices accepted, any tokens the provider
    reported permanently dead (``DeviceNotRegistered``) at send time so the caller
    can reap them, and the ``(receipt_id, token)`` pairs of accepted sends whose
    delivery receipt must be polled later (errors that only surface async)."""

    accepted: int
    invalid_tokens: tuple[str, ...] = ()
    receipts: tuple[tuple[str, str], ...] = ()


class PushTransport(Protocol):
    async def send(self, tokens: Sequence[str], message: PushMessage) -> SendResult:
        """Deliver ``message`` to ``tokens``; report accepted count + dead tokens."""
        ...

    async def get_receipts(self, receipt_ids: Sequence[str]) -> tuple[str, ...]:
        """Poll delivery receipts; return the ids whose token is permanently dead
        (``DeviceNotRegistered``)."""
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
        # No receipts: nothing to poll for the no-op transport.
        return SendResult(accepted=len(tokens))

    async def get_receipts(self, receipt_ids: Sequence[str]) -> tuple[str, ...]:
        return ()


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
        self.receipt_repo = PushReceiptRepository(db)
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
        mute_column = _MUTE_COLUMN_BY_TYPE.get(message.message_type)
        if mute_column:
            tokens = await self.repo.list_tokens_for_users_not_muting(
                user_ids, mute_column
            )
        else:
            tokens = await self.repo.list_tokens_for_users(user_ids)
        return await self._dispatch(tokens, message)

    async def notify_all(self, message: PushMessage) -> int:
        """Broadcast to every registered device — platform-wide announcements
        (PLATFORM_PUSH) and the Hijri-offset change ping (HIJRI_OFFSET). Same
        best-effort contract as ``notify_users``: never raises. PLATFORM_PUSH is
        opt-out-able (mute_promotions); HIJRI_OFFSET is always-on."""
        mute_column = _MUTE_COLUMN_BY_TYPE.get(message.message_type)
        if mute_column:
            tokens = await self.repo.list_all_tokens_not_muting(mute_column)
        else:
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
        if result.receipts:
            # Record accepted sends so the async getReceipts poll can catch a
            # DeviceNotRegistered that only surfaces in the receipt. Best-effort —
            # only ExpoPushTransport returns receipts; LoggingTransport returns none.
            try:
                await self.receipt_repo.create_many(result.receipts)
                await self.receipt_repo.commit()
            except Exception:
                logger.exception(
                    "Failed to persist %d push receipt(s)", len(result.receipts)
                )
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

    async def reap_due_receipts(
        self,
        *,
        older_than_minutes: int = RECEIPT_POLL_DELAY_MINUTES,
        limit: int = RECEIPT_POLL_BATCH,
    ) -> int:
        """Poll Expo for receipts on sends older than ~15 min and prune any token
        the receipt reports ``DeviceNotRegistered`` — failures that only surface
        asynchronously, not in the synchronous send ticket (PRD 03 #0 follow-up).

        Returns the number of tokens reaped. Best-effort and idempotent: every
        polled receipt row is deleted afterwards (checked once), and a no-op
        ``LoggingTransport`` simply finds nothing to do."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
        due = await self.receipt_repo.get_due(cutoff, limit)
        if not due:
            return 0
        try:
            dead_ids = set(
                await self.transport.get_receipts([r.receipt_id for r in due])
            )
        except Exception:
            logger.exception("getReceipts poll failed for %d receipt(s)", len(due))
            return 0

        dead_tokens = {r.token for r in due if r.receipt_id in dead_ids}
        reaped = 0
        try:
            if dead_tokens:
                reaped = await self.repo.delete_tokens(tuple(dead_tokens))
            await self.receipt_repo.delete_by_ids([r.receipt_id for r in due])
            await self.repo.commit()
        except Exception:
            logger.exception(
                "Failed to reap %d dead token(s) from receipts", len(dead_tokens)
            )
        return reaped
