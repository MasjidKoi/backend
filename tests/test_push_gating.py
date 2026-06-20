"""PRD 05 #4 / PRD 09 #28 — per-message-type push gating.

A user can mute non-essential push types; transactional/correctness types have
no switch and always send. Asserted through PushService with a recording
transport (no network), checking which device tokens a fan-out actually reaches.
"""

import uuid
from collections.abc import Sequence

import pytest

from app.models.device_token import DeviceToken
from app.models.enums import PushMessageType
from app.models.user_profile import UserProfile
from app.services.push_service import PushMessage, PushService, SendResult

pytestmark = pytest.mark.asyncio


class RecordingTransport:
    def __init__(self) -> None:
        self.sent_tokens: list[str] = []

    async def send(self, tokens: Sequence[str], message: PushMessage) -> SendResult:
        self.sent_tokens.extend(tokens)
        return SendResult(accepted=len(tokens))

    async def get_receipts(self, receipt_ids: Sequence[str]) -> tuple[str, ...]:
        return ()


async def _device(db, uid: uuid.UUID) -> str:
    token = f"tok-{uuid.uuid4()}"
    db.add(DeviceToken(token=token, user_id=uid, platform="android"))
    await db.flush()
    return token


async def _set(db, uid: uuid.UUID, **flags: bool) -> None:
    profile = await db.get(UserProfile, uid)
    for k, v in flags.items():
        setattr(profile, k, v)
    await db.commit()


def _msg(message_type: PushMessageType) -> PushMessage:
    return PushMessage(message_type=message_type, title="t", body="b")


async def test_gateable_type_skips_muted_user(seed, db):
    muted = await seed.user()
    open_user = await seed.user()
    tok_muted = await _device(db, muted)
    tok_open = await _device(db, open_user)
    await seed.commit()
    await _set(db, muted, mute_donation_nudge=True)

    transport = RecordingTransport()
    await PushService(db, transport=transport).notify_users(
        [muted, open_user], _msg(PushMessageType.RECURRING_NUDGE)
    )

    assert tok_open in transport.sent_tokens
    assert tok_muted not in transport.sent_tokens


async def test_always_on_type_ignores_mutes(seed, db):
    uid = await seed.user()
    tok = await _device(db, uid)
    await seed.commit()
    # Even with every switch flipped, a transactional confirmation still sends.
    await _set(
        db,
        uid,
        mute_donation_nudge=True,
        mute_campaign_milestone=True,
        mute_moderation_outcome=True,
        mute_promotions=True,
    )

    transport = RecordingTransport()
    await PushService(db, transport=transport).notify_users(
        [uid], _msg(PushMessageType.DONATION_CONFIRMED)
    )

    assert tok in transport.sent_tokens


async def test_promotions_broadcast_excludes_muted(seed, db):
    muted = await seed.user()
    open_user = await seed.user()
    tok_muted = await _device(db, muted)
    tok_open = await _device(db, open_user)
    await seed.commit()
    await _set(db, muted, mute_promotions=True)

    transport = RecordingTransport()
    await PushService(db, transport=transport).broadcast_platform_push("Eid", "Mubarak")

    assert tok_open in transport.sent_tokens
    assert tok_muted not in transport.sent_tokens


async def test_owner_without_profile_is_not_muted(seed, db):
    # A device can be registered before the owner ever touches preferences, so it
    # has no user_profiles row. The LEFT JOIN must treat NULL as not-muted and
    # still deliver (NULL IS NOT TRUE → true).
    ghost = uuid.uuid4()
    seed.user_ids.append(ghost)  # no profile created; register for token cleanup
    tok = await _device(db, ghost)
    await db.commit()

    transport = RecordingTransport()
    await PushService(db, transport=transport).broadcast_platform_push("Eid", "Mubarak")

    assert tok in transport.sent_tokens
