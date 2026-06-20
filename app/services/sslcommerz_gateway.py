"""
Async adapter for the SSLCommerz hosted-checkout gateway (PRD 05).

This is the *only* code in the backend that knows SSLCommerz HTTP shapes,
endpoints, and credentials — a sibling of ``gotrue_client.py``. Everything
above it (the donation service, the IPN webhook) talks to a narrow interface
of three calls returning plain dataclasses:

    create_session  → a GatewayPageURL the mobile WebView opens
    validate_ipn    → a verdict the donation service trusts as source of truth
    refund          → an admin-initiated reversal

The HTTP seam is injectable (``transport=``) so tests can fake the boundary
with ``httpx.MockTransport`` rather than monkeypatching mid-module. Money is
reported as Decimal: gross = ``amount``, net = ``store_amount`` (what SSLCommerz
settles to the merchant), fee = gross − net.

SSLCommerz quirks this hides from the rest of the app:
  • session create is ``application/x-www-form-urlencoded`` (not JSON);
  • validation / refund are GET with query params;
  • a 200 response can still mean "invalid transaction" — that is a verdict,
    not an HTTP error, so validate_ipn returns ``is_valid=False`` instead of
    raising. Only transport-level and session-create failures raise.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(15.0)

# Statuses the validation API returns for a genuinely settled payment.
_VALID_STATUSES = frozenset({"VALID", "VALIDATED"})

_TWO_PLACES = Decimal("0.01")


def _to_decimal(value: object) -> Decimal:
    """Parse a gateway money string ('500.00') into a 2-dp Decimal; 0 on junk."""
    try:
        return Decimal(str(value)).quantize(_TWO_PLACES)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


@dataclass(frozen=True, slots=True)
class SessionResult:
    """Outcome of create-session: the URL the WebView opens + the session key."""

    gateway_url: str
    session_key: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """The validation API's verdict on one IPN, parsed and money-normalised.

    ``is_valid`` is True only when the gateway reports a settled status AND the
    response is for our configured store. The donation service still cross-checks
    ``tran_id`` / ``gross`` / ``currency`` against the PENDING row before any
    state change — defence in depth on the most security-sensitive surface.
    """

    is_valid: bool
    status: str
    tran_id: str
    gross: Decimal
    net: Decimal
    fee: Decimal
    currency: str
    store_id: str
    bank_tran_id: str | None
    payment_method: str | None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RefundResult:
    """Outcome of a refund initiation against the validator API."""

    success: bool
    status: str
    refund_ref_id: str | None
    raw: dict = field(default_factory=dict)


class SslcommerzGateway:
    """Wraps the SSLCommerz endpoints MasjidKoi needs.

    Use the module singleton ``sslcommerz`` in production; inject a
    ``transport`` (e.g. ``httpx.MockTransport``) in tests to fake the HTTP seam.
    A fresh ``httpx.AsyncClient`` is opened per call to stay aligned with the
    NullPool / short-lived-connection philosophy.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = settings.sslcommerz_base_url
        self._store_id = settings.sslcommerz_store_id
        self._store_password = settings.sslcommerz_store_password
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport)

    # ── Create session ────────────────────────────────────────────────────────

    async def create_session(
        self,
        *,
        tran_id: str,
        amount: Decimal,
        success_url: str,
        fail_url: str,
        cancel_url: str,
        ipn_url: str,
        customer_name: str,
        customer_email: str,
        product_name: str = "Donation",
        product_category: str = "donation",
    ) -> SessionResult:
        """POST /gwprocess/v4/api.php (form-encoded) → GatewayPageURL.

        Raises HTTPException(502) on a transport error or a FAILED status, so a
        session-create failure surfaces and the donation can be marked FAILED.
        """
        form = {
            "store_id": self._store_id,
            "store_passwd": self._store_password,
            "total_amount": f"{amount:.2f}",
            "currency": "BDT",
            "tran_id": tran_id,
            "success_url": success_url,
            "fail_url": fail_url,
            "cancel_url": cancel_url,
            "ipn_url": ipn_url,
            "shipping_method": "NO",
            "product_name": product_name,
            "product_category": product_category,
            "product_profile": "non-physical-goods",
            "cus_name": customer_name or "Donor",
            "cus_email": customer_email or "donor@masjidkoi.com",
            "cus_add1": "N/A",
            "cus_city": "Dhaka",
            "cus_postcode": "1000",
            "cus_country": "Bangladesh",
            "cus_phone": "01700000000",
            "num_of_item": 1,
        }
        try:
            async with self._client() as client:
                resp = await client.post(
                    f"{self._base}/gwprocess/v4/api.php", data=form
                )
        except httpx.HTTPError as exc:
            logger.error("SSLCommerz create_session transport error: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Payment gateway unreachable",
            ) from exc

        if not resp.is_success:
            logger.error(
                "SSLCommerz create_session HTTP %s: %s", resp.status_code, resp.text
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Payment gateway error",
            )

        body = resp.json()
        if body.get("status") != "SUCCESS" or not body.get("GatewayPageURL"):
            reason = body.get("failedreason") or "unknown error"
            logger.error("SSLCommerz create_session failed: %s", reason)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not start payment: {reason}",
            )

        return SessionResult(
            gateway_url=body["GatewayPageURL"],
            session_key=body.get("sessionkey", ""),
        )

    # ── Validate IPN ────────────────────────────────────────────────────────────

    async def validate_ipn(self, val_id: str) -> ValidationResult:
        """GET /validator/api/validationserverAPI.php?val_id=… → verdict.

        Never raises on a business verdict — an invalid / forged notification
        comes back as ``is_valid=False`` so the service can mark the donation
        FAILED. Only a transport error raises (the IPN handler treats that as
        retryable rather than completing the donation).
        """
        params = {
            "val_id": val_id,
            "store_id": self._store_id,
            "store_passwd": self._store_password,
            "v": "1",
            "format": "json",
        }
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{self._base}/validator/api/validationserverAPI.php",
                    params=params,
                )
        except httpx.HTTPError as exc:
            logger.error("SSLCommerz validate_ipn transport error: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Payment validation unreachable",
            ) from exc

        if not resp.is_success:
            logger.error(
                "SSLCommerz validate_ipn HTTP %s: %s", resp.status_code, resp.text
            )
            return ValidationResult(
                is_valid=False,
                status=f"http_{resp.status_code}",
                tran_id="",
                gross=Decimal("0.00"),
                net=Decimal("0.00"),
                fee=Decimal("0.00"),
                currency="",
                store_id="",
                bank_tran_id=None,
                payment_method=None,
                raw={},
            )

        body = resp.json()
        gross = _to_decimal(body.get("amount"))
        net = _to_decimal(body.get("store_amount"))
        # Net (what the merchant is credited) can never exceed gross; clamp to
        # guard against a gateway quirk returning store_amount > amount, which
        # would otherwise over-credit the masjid via a negative "fee".
        if net > gross:
            net = gross
        fee = (gross - net).quantize(_TWO_PLACES)
        gateway_status = str(body.get("status", ""))
        store_id = str(body.get("store_id", "") or "")
        store_matches = (not store_id) or store_id == self._store_id

        return ValidationResult(
            is_valid=(gateway_status in _VALID_STATUSES) and store_matches,
            status=gateway_status,
            tran_id=str(body.get("tran_id", "")),
            gross=gross,
            net=net,
            fee=fee,
            currency=str(body.get("currency", "")),
            store_id=store_id,
            bank_tran_id=body.get("bank_tran_id"),
            payment_method=body.get("card_type") or body.get("card_issuer"),
            raw=body,
        )

    # ── Refund ────────────────────────────────────────────────────────────────

    async def refund(
        self, *, bank_tran_id: str, amount: Decimal, reason: str
    ) -> RefundResult:
        """GET /validator/api/merchantTransIDvalidationAPI.php?refund… → result.

        SSLCommerz refunds are async on their side: a ``success`` /
        ``processing`` status means the request was accepted; failure raises so
        the admin sees it and can fall back to a manual reversal.
        """
        params = {
            "bank_tran_id": bank_tran_id,
            "refund_amount": f"{amount:.2f}",
            "refund_remarks": reason or "Refund",
            "store_id": self._store_id,
            "store_passwd": self._store_password,
            "v": "1",
            "format": "json",
        }
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{self._base}/validator/api/merchantTransIDvalidationAPI.php",
                    params=params,
                )
        except httpx.HTTPError as exc:
            logger.error("SSLCommerz refund transport error: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Refund gateway unreachable",
            ) from exc

        if not resp.is_success:
            logger.error("SSLCommerz refund HTTP %s: %s", resp.status_code, resp.text)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Refund gateway error",
            )

        body = resp.json()
        refund_status = str(body.get("status", "")).lower()
        return RefundResult(
            success=refund_status in {"success", "processing"},
            status=refund_status,
            refund_ref_id=body.get("refund_ref_id"),
            raw=body,
        )


# ── Singleton for use in dependencies ─────────────────────────────────────────

sslcommerz = SslcommerzGateway()
