"""SSLCommerz payment callbacks (PRD 05) — the most security-sensitive surface.

Two unauthenticated, publicly-reachable endpoints:

  POST /payments/sslcommerz/ipn        the server-to-server notification — the
                                       ONLY writer of COMPLETED. Trusts nothing:
                                       every notification is re-validated against
                                       the SSLCommerz validation API, and the
                                       store-id/amount/currency must match the
                                       pending row (enforced in the service).
                                       Rate-limited; idempotent on val_id.
  GET  /payments/sslcommerz/redirect/{outcome}
                                       navigation only — 302s the WebView back
                                       into the app via a deep link. NEVER a
                                       source of truth; the app polls status.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.config import settings
from app.core.rate_limit import make_rate_limiter
from app.dependencies.donation import get_donation_service
from app.services.donation_service import DonationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments/sslcommerz", tags=["payments"])

# Generous limit per IP — the gateway may legitimately fan IPNs, but a flood is
# suspicious. Degrades gracefully if Redis is down.
_ipn_limiter = make_rate_limiter(limit=120, window_s=60, key_prefix="sslcommerz_ipn")
# The redirect is unauthenticated and, on a success POST, triggers the same
# outbound gateway validation as the IPN — so it gets the same per-IP cap the IPN
# has, instead of being an unlimited amplification/DoS surface (CODEBASE_AUDIT #8).
_redirect_limiter = make_rate_limiter(
    limit=120, window_s=60, key_prefix="sslcommerz_redirect"
)

_OUTCOMES = {"success", "fail", "cancel"}


@router.post(
    "/ipn",
    summary="SSLCommerz IPN — validates and completes (the sole COMPLETED writer)",
)
async def sslcommerz_ipn(
    request: Request,
    _rl: None = Depends(_ipn_limiter),
    service: DonationService = Depends(get_donation_service),
) -> JSONResponse:
    form = await request.form()
    val_id = str(form.get("val_id", ""))
    tran_id = str(form.get("tran_id", ""))
    if not val_id or not tran_id:
        # Log field names only — never the full form body (it can carry card /
        # account fragments and the gateway's own identifiers) on a payment surface.
        logger.warning("IPN missing val_id/tran_id; fields=%s", sorted(form.keys()))
        # Malformed — don't ask the gateway to retry.
        return JSONResponse({"status": "ignored"}, status_code=status.HTTP_200_OK)

    try:
        donation = await service.complete_from_ipn(val_id=val_id, tran_id=tran_id)
    except Exception as exc:  # noqa: BLE001
        if (
            isinstance(exc, HTTPException)
            and exc.status_code == status.HTTP_502_BAD_GATEWAY
        ):
            # Validation API unreachable — ask SSLCommerz to retry the IPN.
            logger.warning("IPN deferred (gateway unreachable) tran=%s", tran_id)
            return JSONResponse(
                {"status": "retry"},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if isinstance(exc, HTTPException):
            # Forged / mismatched / terminal — handled, do not retry.
            logger.warning("IPN rejected tran=%s: %s", tran_id, exc.detail)
            return JSONResponse({"status": "rejected"}, status_code=status.HTTP_200_OK)
        # Unexpected (e.g. transient DB error) — let the gateway retry.
        logger.exception("IPN processing error tran=%s", tran_id)
        return JSONResponse(
            {"status": "retry"}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE
        )

    return JSONResponse(
        {"status": donation.status, "donation_id": str(donation.donation_id)},
        status_code=status.HTTP_200_OK,
    )


@router.api_route(
    "/redirect/{outcome}",
    methods=["GET", "POST"],
    summary="Post-payment redirect → deep link into the app",
)
async def sslcommerz_redirect(
    outcome: str,
    request: Request,
    donation_id: str = "",
    _rl: None = Depends(_redirect_limiter),
    service: DonationService = Depends(get_donation_service),
) -> RedirectResponse:
    """SSLCommerz redirects here by **POST** (form-encoded transaction fields) —
    hence GET and POST are both accepted (a GET-only route 405s the gateway).

    The IPN remains the authoritative completer, but on a success POST we also
    complete idempotently from the carried val_id. This is NOT trusting the
    client: complete_from_ipn re-validates the val_id against the SSLCommerz
    validation API exactly as the IPN path does. It makes success robust when
    the IPN is delayed or the gateway's IPN listener isn't configured; the IPN
    still covers the case where the user closes the browser before redirect.
    """
    outcome = outcome if outcome in _OUTCOMES else "fail"
    tran_id = donation_id
    if request.method == "POST":
        form = await request.form()
        val_id = str(form.get("val_id", "") or "")
        tran_id = str(form.get("tran_id", "") or donation_id or "")
        if outcome == "success" and val_id and tran_id:
            try:
                await service.complete_from_ipn(val_id=val_id, tran_id=tran_id)
            except Exception:  # noqa: BLE001
                # Best-effort — the server-to-server IPN is the backstop.
                logger.warning(
                    "redirect-path completion deferred to IPN for tran=%s", tran_id
                )
        elif outcome in ("fail", "cancel") and tran_id:
            # SSLCommerz sends no IPN for a declined/cancelled payment, so the
            # redirect is the only timely signal — persist PENDING → FAILED here
            # rather than leaving the row PENDING until the 24h stale sweep.
            # Safe: fail_from_redirect locks the row and only writes when it is
            # still PENDING, so a valid IPN that completed first is never lost.
            try:
                await service.fail_from_redirect(tran_id=tran_id, reason=outcome)
            except Exception:  # noqa: BLE001
                # Best-effort — the stale sweep remains the backstop.
                logger.warning(
                    "redirect-path fail/cancel write deferred to sweep for tran=%s",
                    tran_id,
                )

    # Validate the id as a UUID so a crafted value can't inject extra path/query
    # segments into the deep-link Location header.
    try:
        safe_id = str(uuid.UUID(tran_id)) if tran_id else ""
    except ValueError:
        safe_id = ""
    scheme = settings.APP_DEEP_LINK_SCHEME
    target = f"{scheme}://donation/{safe_id}?status={outcome}"
    # 303 so the browser issues a GET to the deep link regardless of the inbound method.
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
