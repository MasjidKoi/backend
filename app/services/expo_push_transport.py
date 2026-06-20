"""Expo Push Service transport — the real wire for PRD 03 push delivery.

This is the *only* code in the backend that knows the Expo Push HTTP shape, a
sibling of ``sslcommerz_gateway.py`` / ``gotrue_client.py``. ``PushService``
talks to it through the ``PushTransport`` Protocol; selection between this and
the no-op ``LoggingTransport`` is config-driven (``PUSH_ENABLED``).

Expo matches the mobile ``expo-notifications`` choice: the device tokens we
register are ``ExponentPushToken[…]`` and Expo relays to APNs/FCM for us, so the
backend needs no Firebase/APNs credentials — at most an optional access token
(enhanced security). The HTTP seam is injectable (``transport=``) so tests fake
it with ``httpx.MockTransport`` rather than monkeypatching mid-module.

Expo quirks this hides from the rest of the app:
  • at most 100 messages per request — we chunk;
  • tickets come back in send order, so ticket[i] maps to the i-th token;
  • a per-ticket ``DeviceNotRegistered`` error means that token is permanently
    dead (app uninstalled / token rotated) — we surface those for pruning;
  • only ``ExponentPushToken[…]`` tokens are deliverable; web-platform tokens
    are skipped rather than sent.
"""

import logging
from collections.abc import Sequence
from itertools import islice

import httpx

from app.services.push_service import PushMessage, SendResult

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(15.0)
_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
# Expo accepts at most 100 messages per request.
_BATCH_SIZE = 100
# Deliverable Expo push tokens carry one of these prefixes.
_EXPO_TOKEN_PREFIXES = ("ExponentPushToken[", "ExpoPushToken[")


def _chunked(items: list[str], size: int):
    it = iter(items)
    while batch := list(islice(it, size)):
        yield batch


class ExpoPushTransport:
    """Delivers a :class:`PushMessage` to Expo push tokens. Satisfies the
    ``PushTransport`` Protocol structurally.

    A fresh ``httpx.AsyncClient`` is opened per call to stay aligned with the
    NullPool / short-lived-connection philosophy.
    """

    def __init__(
        self,
        access_token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._access_token = access_token or None
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return httpx.AsyncClient(
            timeout=_TIMEOUT, transport=self._transport, headers=headers
        )

    async def send(self, tokens: Sequence[str], message: PushMessage) -> SendResult:
        deliverable = [t for t in tokens if t.startswith(_EXPO_TOKEN_PREFIXES)]
        skipped = len(tokens) - len(deliverable)
        if skipped:
            logger.info(
                "Push: skipping %d non-Expo token(s) for type=%s",
                skipped,
                message.message_type.value,
            )
        if not deliverable:
            return SendResult(accepted=0)

        accepted = 0
        invalid: list[str] = []
        async with self._client() as client:
            for chunk in _chunked(deliverable, _BATCH_SIZE):
                payload = [
                    {
                        "to": token,
                        "title": message.title,
                        "body": message.body,
                        "data": message.data,
                        "sound": "default",
                    }
                    for token in chunk
                ]
                resp = await client.post(_EXPO_PUSH_URL, json=payload)
                resp.raise_for_status()
                tickets = resp.json().get("data") or []
                if len(tickets) != len(chunk):
                    logger.warning(
                        "Push: Expo returned %d ticket(s) for %d token(s)",
                        len(tickets),
                        len(chunk),
                    )
                for token, ticket in zip(chunk, tickets, strict=False):
                    if ticket.get("status") == "ok":
                        accepted += 1
                    elif (ticket.get("details", {}) or {}).get(
                        "error"
                    ) == "DeviceNotRegistered":
                        invalid.append(token)
        return SendResult(accepted=accepted, invalid_tokens=tuple(invalid))
