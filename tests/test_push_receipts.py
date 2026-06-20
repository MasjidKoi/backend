"""PushService.reap_due_receipts tests (PRD 03 #0 follow-up).

Drives the async dead-token reaper over real rows with a fake transport (the
Expo wire itself is covered in test_expo_push_transport.py). Pins: only receipts
past the poll delay are checked, a DeviceNotRegistered receipt prunes its token,
an ok receipt keeps its token, and every checked receipt row is cleared.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.models.device_token import DeviceToken
from app.models.push_receipt import PushReceipt
from app.services.push_service import PushMessage, PushService, SendResult


class _FakeTransport:
    """Returns a fixed set of receipt ids as DeviceNotRegistered."""

    def __init__(self, dead_ids: Sequence[str]) -> None:
        self.dead_ids = set(dead_ids)
        self.polled: list[str] | None = None

    async def send(
        self, tokens, message: PushMessage
    ) -> SendResult:  # pragma: no cover
        return SendResult(accepted=len(tokens))

    async def get_receipts(self, receipt_ids: Sequence[str]) -> tuple[str, ...]:
        self.polled = list(receipt_ids)
        return tuple(r for r in receipt_ids if r in self.dead_ids)


async def _add_token(db, user_id: uuid.UUID, token: str) -> None:
    db.add(DeviceToken(token=token, user_id=user_id, platform="android"))
    await db.flush()


async def _add_receipt(db, receipt_id: str, token: str, age_minutes: int) -> None:
    db.add(
        PushReceipt(
            receipt_id=receipt_id,
            token=token,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
        )
    )
    await db.flush()


async def _token_exists(db, token: str) -> bool:
    row = await db.execute(select(DeviceToken).where(DeviceToken.token == token))
    return row.scalar_one_or_none() is not None


async def _receipt_exists(db, receipt_id: str) -> bool:
    row = await db.execute(
        select(PushReceipt).where(PushReceipt.receipt_id == receipt_id)
    )
    return row.scalar_one_or_none() is not None


async def test_reap_prunes_dead_keeps_live_clears_checked(db, seed):
    uid = await seed.user()
    sfx = uuid.uuid4().hex  # unique ids so the run never collides with others
    tok_dead = f"ExponentPushToken[dead-{sfx}]"
    tok_ok = f"ExponentPushToken[ok-{sfx}]"
    tok_fresh = f"ExponentPushToken[fresh-{sfx}]"
    r_dead, r_ok, r_fresh = f"rd-{sfx}", f"ro-{sfx}", f"rf-{sfx}"

    for t in (tok_dead, tok_ok, tok_fresh):
        await _add_token(db, uid, t)
    # Two receipts past the 15-min poll delay, one still fresh.
    await _add_receipt(db, r_dead, tok_dead, age_minutes=20)
    await _add_receipt(db, r_ok, tok_ok, age_minutes=20)
    await _add_receipt(db, r_fresh, tok_fresh, age_minutes=1)
    await seed.commit()

    transport = _FakeTransport(dead_ids=[r_dead])
    reaped = await PushService(db, transport=transport).reap_due_receipts()

    try:
        assert reaped == 1
        # Only the two due receipts were polled — the fresh one was skipped.
        assert set(transport.polled) == {r_dead, r_ok}
        # Dead token reaped; the ok and fresh tokens survive.
        assert await _token_exists(db, tok_dead) is False
        assert await _token_exists(db, tok_ok) is True
        assert await _token_exists(db, tok_fresh) is True
        # Both checked receipt rows cleared; the fresh one remains for next poll.
        assert await _receipt_exists(db, r_dead) is False
        assert await _receipt_exists(db, r_ok) is False
        assert await _receipt_exists(db, r_fresh) is True
    finally:
        await db.execute(
            delete(PushReceipt).where(
                PushReceipt.receipt_id.in_([r_dead, r_ok, r_fresh])
            )
        )
        await db.commit()


async def test_reap_noop_when_nothing_due(db, seed):
    uid = await seed.user()
    sfx = uuid.uuid4().hex
    r_fresh = f"rf-{sfx}"
    await _add_token(db, uid, f"ExponentPushToken[x-{sfx}]")
    await _add_receipt(db, r_fresh, f"ExponentPushToken[x-{sfx}]", age_minutes=1)
    await seed.commit()

    transport = _FakeTransport(dead_ids=[r_fresh])
    reaped = await PushService(db, transport=transport).reap_due_receipts()

    try:
        assert reaped == 0
        assert transport.polled is None  # nothing due → no poll
        assert await _receipt_exists(db, r_fresh) is True
    finally:
        await db.execute(delete(PushReceipt).where(PushReceipt.receipt_id == r_fresh))
        await db.commit()
