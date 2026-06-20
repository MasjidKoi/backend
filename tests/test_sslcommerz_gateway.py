"""Tests for SslcommerzGateway — the SSLCommerz HTTP/adapter seam (PRD 05).

The gateway is tested against a faked HTTP layer (``httpx.MockTransport``),
never by monkeypatching mid-module. We assert external behaviour through the
adapter's public interface: the session URL it returns, the verdict it reports,
and the money it parses — not how it builds requests.
"""

from decimal import Decimal

import httpx
import pytest
from fastapi import HTTPException

from app.services.sslcommerz_gateway import SslcommerzGateway

pytestmark = pytest.mark.asyncio


def _gateway(handler) -> SslcommerzGateway:
    """Build a gateway whose HTTP calls are served by `handler(request)`."""
    return SslcommerzGateway(transport=httpx.MockTransport(handler))


# ── create_session ────────────────────────────────────────────────────────────


async def test_create_session_success_returns_gateway_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/gwprocess/v4/api.php")
        assert request.method == "POST"
        # Form-encoded, carries our tran_id.
        assert b"tran_id=abc-123" in request.content
        return httpx.Response(
            200,
            json={
                "status": "SUCCESS",
                "sessionkey": "sess-key-xyz",
                "GatewayPageURL": "https://sandbox.sslcommerz.com/pay/abc-123",
            },
        )

    result = await _gateway(handler).create_session(
        tran_id="abc-123",
        amount=Decimal("500.00"),
        success_url="https://api/success",
        fail_url="https://api/fail",
        cancel_url="https://api/cancel",
        ipn_url="https://api/ipn",
        customer_name="Donor",
        customer_email="donor@example.com",
    )

    assert result.gateway_url == "https://sandbox.sslcommerz.com/pay/abc-123"
    assert result.session_key == "sess-key-xyz"


async def test_create_session_failed_status_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "FAILED", "failedreason": "Invalid store credentials"}
        )

    with pytest.raises(HTTPException) as exc:
        await _gateway(handler).create_session(
            tran_id="abc-123",
            amount=Decimal("500.00"),
            success_url="s",
            fail_url="f",
            cancel_url="c",
            ipn_url="i",
            customer_name="Donor",
            customer_email="donor@example.com",
        )
    assert exc.value.status_code == 502


async def test_create_session_http_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream down")

    with pytest.raises(HTTPException) as exc:
        await _gateway(handler).create_session(
            tran_id="abc-123",
            amount=Decimal("500.00"),
            success_url="s",
            fail_url="f",
            cancel_url="c",
            ipn_url="i",
            customer_name="Donor",
            customer_email="donor@example.com",
        )
    assert exc.value.status_code == 502


# ── validate_ipn ────────────────────────────────────────────────────────────


async def test_validate_ipn_valid_parses_money():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/validator/api/validationserverAPI.php")
        assert request.url.params.get("val_id") == "VAL-1"
        return httpx.Response(
            200,
            json={
                "status": "VALID",
                "tran_id": "abc-123",
                "val_id": "VAL-1",
                "amount": "500.00",
                "store_amount": "487.75",
                "currency": "BDT",
                "bank_tran_id": "BANK-9",
                "card_type": "BKASH-bKash",
            },
        )

    r = await _gateway(handler).validate_ipn("VAL-1")
    assert r.is_valid is True
    assert r.tran_id == "abc-123"
    assert r.gross == Decimal("500.00")
    assert r.net == Decimal("487.75")
    assert r.fee == Decimal("12.25")  # gross − net
    assert r.currency == "BDT"
    assert r.bank_tran_id == "BANK-9"
    assert r.payment_method == "BKASH-bKash"


async def test_validate_ipn_validated_status_also_valid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "VALIDATED",
                "tran_id": "abc-123",
                "amount": "100.00",
                "store_amount": "97.50",
                "currency": "BDT",
            },
        )

    r = await _gateway(handler).validate_ipn("VAL-1")
    assert r.is_valid is True
    assert r.fee == Decimal("2.50")


async def test_validate_ipn_clamps_net_above_gross():
    # Gateway quirk: store_amount > amount would imply a negative fee and
    # over-credit the masjid. The adapter must clamp net to gross.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "VALID",
                "tran_id": "abc-123",
                "amount": "500.00",
                "store_amount": "600.00",
                "currency": "BDT",
            },
        )

    r = await _gateway(handler).validate_ipn("VAL-1")
    assert r.is_valid is True
    assert r.net == Decimal("500.00")  # clamped to gross
    assert r.fee == Decimal("0.00")  # never negative


async def test_validate_ipn_invalid_transaction_not_valid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "INVALID_TRANSACTION", "tran_id": "abc-123"}
        )

    r = await _gateway(handler).validate_ipn("VAL-1")
    assert r.is_valid is False
    assert r.status == "INVALID_TRANSACTION"


async def test_validate_ipn_store_mismatch_not_valid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "VALID",
                "tran_id": "abc-123",
                "amount": "500.00",
                "store_amount": "487.75",
                "currency": "BDT",
                "store_id": "someone_elses_store",
            },
        )

    r = await _gateway(handler).validate_ipn("VAL-1")
    # A settled status but for the wrong store is forged from our point of view.
    assert r.is_valid is False


async def test_validate_ipn_http_error_not_valid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="validator down")

    r = await _gateway(handler).validate_ipn("VAL-1")
    assert r.is_valid is False


# ── refund ────────────────────────────────────────────────────────────────


async def test_refund_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(
            "/validator/api/merchantTransIDvalidationAPI.php"
        )
        assert request.url.params.get("bank_tran_id") == "BANK-9"
        assert request.url.params.get("refund_amount") == "500.00"
        return httpx.Response(200, json={"status": "success", "refund_ref_id": "REF-1"})

    r = await _gateway(handler).refund(
        bank_tran_id="BANK-9", amount=Decimal("500.00"), reason="duplicate"
    )
    assert r.success is True
    assert r.refund_ref_id == "REF-1"


async def test_refund_failed_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "failed"})

    r = await _gateway(handler).refund(
        bank_tran_id="BANK-9", amount=Decimal("500.00"), reason="duplicate"
    )
    assert r.success is False
