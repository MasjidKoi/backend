"""Tests for ExpoPushTransport — the Expo Push HTTP seam (PRD 03 push delivery).

The transport is tested against a faked HTTP layer (``httpx.MockTransport``),
never by monkeypatching mid-module. We assert external behaviour through the
public ``send`` interface: how many devices it reports accepted, which tokens it
surfaces as dead, that it batches, that it skips non-Expo tokens, and that it
authenticates only when given a token — not how it builds requests internally.
"""

import json

import httpx
import pytest

from app.models.enums import PushMessageType
from app.services.expo_push_transport import ExpoPushTransport
from app.services.push_service import PushMessage

pytestmark = pytest.mark.asyncio


def _message() -> PushMessage:
    return PushMessage(
        message_type=PushMessageType.ANNOUNCEMENT_INSTANT,
        title="Jumu'ah moved",
        body="This week's Jumu'ah is at 1:30 PM.",
        data={"masjid_id": "abc"},
    )


def _transport(handler) -> ExpoPushTransport:
    """Build a transport whose HTTP calls are served by `handler(request)`."""
    return ExpoPushTransport(
        access_token="tok-123", transport=httpx.MockTransport(handler)
    )


def _expo_token(n: int) -> str:
    return f"ExponentPushToken[{n:032d}]"


async def test_all_ok_reports_accepted_and_no_invalid():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://exp.host/--/api/v2/push/send")
        assert request.method == "POST"
        msgs = json.loads(request.content)
        # One message per token, carrying our payload.
        assert all(m["title"] == "Jumu'ah moved" for m in msgs)
        assert all(m["data"] == {"masjid_id": "abc"} for m in msgs)
        return httpx.Response(
            200, json={"data": [{"status": "ok", "id": "x"} for _ in msgs]}
        )

    result = await _transport(handler).send(
        [_expo_token(1), _expo_token(2)], _message()
    )

    assert result.accepted == 2
    assert result.invalid_tokens == ()


async def test_device_not_registered_is_surfaced_for_pruning():
    dead = _expo_token(2)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"status": "ok", "id": "x"},
                    {
                        "status": "error",
                        "message": "gone",
                        "details": {"error": "DeviceNotRegistered"},
                    },
                ]
            },
        )

    result = await _transport(handler).send([_expo_token(1), dead], _message())

    assert result.accepted == 1
    assert result.invalid_tokens == (dead,)


async def test_other_errors_are_not_pruned():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "status": "error",
                        "message": "too big",
                        "details": {"error": "MessageTooBig"},
                    }
                ]
            },
        )

    result = await _transport(handler).send([_expo_token(1)], _message())

    assert result.accepted == 0
    assert result.invalid_tokens == ()  # not DeviceNotRegistered → kept


async def test_batches_at_100_per_request():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        msgs = json.loads(request.content)
        calls.append(len(msgs))
        return httpx.Response(
            200, json={"data": [{"status": "ok", "id": "x"} for _ in msgs]}
        )

    tokens = [_expo_token(i) for i in range(250)]
    result = await _transport(handler).send(tokens, _message())

    assert result.accepted == 250
    assert calls == [100, 100, 50]  # three POSTs, chunked


async def test_non_expo_tokens_are_skipped():
    def handler(request: httpx.Request) -> httpx.Response:
        msgs = json.loads(request.content)
        # Only the one real Expo token should ever reach the wire.
        assert len(msgs) == 1
        return httpx.Response(200, json={"data": [{"status": "ok", "id": "x"}]})

    result = await _transport(handler).send(
        ["web-fcm-token-xyz", _expo_token(1)], _message()
    )

    assert result.accepted == 1


async def test_all_tokens_non_expo_skips_network_entirely():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not POST when nothing is deliverable")

    result = await _transport(handler).send(["web-only-token"], _message())

    assert result.accepted == 0
    assert result.invalid_tokens == ()


async def test_authorization_header_present_only_with_token():
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"status": "ok", "id": "x"}]})

    mock = httpx.MockTransport(handler)

    await ExpoPushTransport(access_token="secret", transport=mock).send(
        [_expo_token(1)], _message()
    )
    assert seen["auth"] == "Bearer secret"

    await ExpoPushTransport(access_token=None, transport=mock).send(
        [_expo_token(1)], _message()
    )
    assert seen["auth"] is None
