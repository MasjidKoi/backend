"""DonationLedger — the money-correctness core of the donation system (PRD 05).

This service owns the donation state machine, the gross/fee/net arithmetic, the
atomic campaign-counter update, transaction-ID idempotency, and the post-commit
side effects. Everything money depends on lives behind its narrow interface:

    create_pending     PENDING row + a gateway URL the WebView opens
    complete_from_ipn  the SOLE writer of COMPLETED — validated, idempotent
    fail               PENDING → FAILED (IPN fail/cancel or the stale sweep)
    refund             COMPLETED → REFUNDED (admin only; see Step 4)
    balance_of         derived per-masjid balance (see Step 4)

State machine (each transition is one transaction):

    create  → PENDING    (row inserted before the gateway session call;
                          session-create failure → FAILED)
    IPN ok  → COMPLETED  (fee/net set, campaign counter bumped, receipt number
                          allocated; idempotent on the unique gateway_val_id)
    IPN bad → FAILED
    refund  → REFUNDED   (only from COMPLETED)

FAILED is terminal — a retry is a new donation, prefilled client-side.
"""

import base64
import binascii
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import CurrentUser
from app.core.time import DHAKA_TZ
from app.models.disbursement import Disbursement
from app.models.donation import Donation
from app.models.enums import (
    DonationCategory,
    DonationStatus,
    MasjidStatus,
    PushMessageType,
)
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.disbursement_repository import DisbursementRepository
from app.repositories.donation_repository import DonationRepository
from app.repositories.masjid_campaign_repository import MasjidCampaignRepository
from app.repositories.masjid_repository import MasjidRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.schemas.disbursement import (
    BalanceListResponse,
    BalanceResponse,
    MasjidBalanceItem,
)
from app.schemas.donation import (
    AdminDonationItem,
    AdminDonationListResponse,
    DonationHistoryItem,
    DonationHistoryResponse,
    DonationStatusResponse,
    DonationSummaryResponse,
    PerMasjidTotal,
)
from app.services.sslcommerz_gateway import SslcommerzGateway, sslcommerz

logger = logging.getLogger(__name__)

MIN_AMOUNT = Decimal("10")
MAX_AMOUNT = Decimal("500000")
_TWO_PLACES = Decimal("0.01")


def _encode_history_cursor(created_at: datetime, donation_id: uuid.UUID) -> str:
    """Opaque keyset cursor carrying the full stable sort key
    ``(created_at, donation_id)`` so pagination is tie-safe (mirrors the feed)."""
    raw = f"{created_at.isoformat()}|{donation_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_history_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        parts = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        if len(parts) != 2:
            raise ValueError("expected 2 parts")
        return datetime.fromisoformat(parts[0]), uuid.UUID(parts[1])
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor"
        ) from exc


@dataclass(frozen=True, slots=True)
class CheckoutResult:
    """What checkout-init hands back: the PENDING donation + the gateway URL."""

    donation: Donation
    gateway_url: str


class DonationService:
    def __init__(
        self, db: AsyncSession, gateway: SslcommerzGateway | None = None
    ) -> None:
        self.db = db
        self.repo = DonationRepository(db)
        self.masjid_repo = MasjidRepository(db)
        self.campaign_repo = MasjidCampaignRepository(db)
        self.disbursement_repo = DisbursementRepository(db)
        self.profile_repo = UserProfileRepository(db)
        self.audit = AuditLogRepository(db)
        # Injectable so tests fake the gateway at its public seam.
        self.gateway = gateway or sslcommerz

    # ── Estimate (pre-confirm display only) ───────────────────────────────────

    @staticmethod
    def estimate_net(gross: Decimal) -> Decimal:
        """Approximate "masjid receives ~৳X" from the configured fee rate.

        The ledger stores the ACTUAL fee from the validated IPN; this is only a
        pre-confirm estimate and the two may differ by a taka or two — donor copy
        says "approx".
        """
        fee = (gross * Decimal(str(settings.SSLCOMMERZ_FEE_RATE))).quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP
        )
        return (gross - fee).quantize(_TWO_PLACES)

    # ── Create checkout (→ PENDING) ───────────────────────────────────────────

    async def create_pending(
        self,
        user: CurrentUser,
        *,
        masjid_id: uuid.UUID | None = None,
        amount: Decimal,
        category: str,
        is_anonymous: bool | None = None,
        donor_name: str | None = None,
        campaign_id: uuid.UUID | None = None,
    ) -> CheckoutResult:
        """Validate, insert a PENDING donation, open a gateway session.

        For a campaign donation, ``masjid_id`` is **derived from the campaign**
        and the category is forced to CAMPAIGN, so a client can never aim a
        campaign donation at the wrong masjid.
        """
        amount = Decimal(amount).quantize(_TWO_PLACES)
        if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Donation must be between ৳10 and ৳5,00,000",
            )

        if campaign_id is not None:
            campaign = await self.campaign_repo.get_by_id(campaign_id)
            if campaign is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found"
                )
            today = datetime.now(DHAKA_TZ).date()
            if campaign.status != "Active" or not (
                campaign.start_date <= today <= campaign.end_date
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Campaign is not currently accepting donations",
                )
            masjid_id = campaign.masjid_id  # derive — never client-supplied
            category_value = DonationCategory.CAMPAIGN.value
        else:
            if masjid_id is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="masjid_id or campaign_id is required",
                )
            try:
                category_value = DonationCategory(category).value
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Unknown donation category",
                )
            if category_value == DonationCategory.CAMPAIGN.value:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Campaign category requires a campaign donation",
                )

        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if masjid is None or masjid.status != MasjidStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Masjid not found or not active",
            )
        if not masjid.donations_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This masjid is not accepting donations",
            )

        # PRD 05 #3 — when the client doesn't state a preference, seed anonymity
        # from the user's donate_anonymously_by_default setting; an explicit value
        # always wins.
        if is_anonymous is None:
            profile = await self.profile_repo.get_or_create(user.user_id, user.email)
            is_anonymous = profile.donate_anonymously_by_default

        donation = Donation(
            user_id=user.user_id,
            masjid_id=masjid_id,
            campaign_id=campaign_id,
            category=category_value,
            status=DonationStatus.PENDING.value,
            gross_amount=amount,
            fee_amount=Decimal("0.00"),
            net_amount=amount,  # net = gross at PENDING (fee unknown until IPN)
            is_anonymous=is_anonymous,
            donor_name=donor_name,
            donor_email=user.email,
        )
        await self.repo.add(donation)  # flush → donation_id available as tran_id
        # Commit the PENDING row BEFORE the up-to-15s SSLCommerz create_session
        # call so we don't hold a transaction — and thus pin a PgBouncer server
        # connection — across external I/O on every checkout (mirrors the release
        # complete_from_ipn does before validate_ipn). donation_id is a Python-side
        # uuid4 assigned at flush, so it is already available as the tran_id below.
        await self.repo.commit()

        base = settings.public_api_base_url
        did = str(donation.donation_id)
        try:
            session_result = await self.gateway.create_session(
                tran_id=did,
                amount=amount,
                success_url=f"{base}/payments/sslcommerz/redirect/success?donation_id={did}",
                fail_url=f"{base}/payments/sslcommerz/redirect/fail?donation_id={did}",
                cancel_url=f"{base}/payments/sslcommerz/redirect/cancel?donation_id={did}",
                ipn_url=f"{base}/payments/sslcommerz/ipn",
                customer_name=donor_name or "Donor",
                customer_email=user.email or "donor@masjidkoi.com",
                product_name=f"Donation to {masjid.name}"[:255],
            )
        except HTTPException:
            # Session-create failure → the donation never had a payable URL.
            donation.status = DonationStatus.FAILED.value
            await self.repo.commit()
            logger.warning(
                "donation session-create failed → FAILED",
                extra={
                    "event": "donation_session_create_failed",
                    "donation_id": did,
                    "masjid_id": str(masjid_id),
                    "gross": str(amount),
                },
            )
            raise

        donation.gateway_session_key = session_result.session_key
        await self.repo.commit()
        await self.repo.refresh(donation)
        logger.info(
            "donation created (PENDING)",
            extra={
                "event": "donation_created",
                "donation_id": did,
                "user_id": str(user.user_id),
                "masjid_id": str(masjid_id),
                "campaign_id": str(campaign_id) if campaign_id else None,
                "category": category_value,
                "gross": str(amount),
            },
        )
        return CheckoutResult(donation=donation, gateway_url=session_result.gateway_url)

    # ── Complete from validated IPN (→ COMPLETED) ─────────────────────────────

    async def complete_from_ipn(self, *, val_id: str, tran_id: str) -> Donation:
        """The ONLY writer of COMPLETED. Re-validates every notification against
        the SSLCommerz validation API before any state change, and is idempotent:
        a replayed/duplicate IPN on an already-completed donation is a no-op.
        """
        try:
            donation_id = uuid.UUID(str(tran_id))
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid transaction id",
            )

        donation = await self.repo.get_by_id(donation_id)
        if donation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Donation not found"
            )

        # Idempotent no-op: a duplicate/replayed IPN changes nothing.
        if donation.status == DonationStatus.COMPLETED.value:
            return donation
        if donation.status != DonationStatus.PENDING.value:
            # Terminal state (FAILED/REFUNDED): completing would be illegal.
            # A valid IPN here means money may have moved after a stale-fail —
            # surface it loudly for manual reconciliation rather than guessing.
            logger.error(
                "IPN for donation %s in terminal state %s — manual review",
                donation_id,
                donation.status,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Donation is not awaiting payment",
            )

        # End the read transaction BEFORE the external validation call so its
        # PgBouncer server connection is returned to the pool. Holding it open
        # across up to 15s of gateway I/O would pin one of the pool's 20 server
        # connections per in-flight IPN and starve the rest of the app under a
        # burst. commit() (not rollback) because expire_on_commit=False keeps the
        # already-read row usable; only a read happened, so this writes nothing.
        # The authoritative state is re-read below under a row lock regardless.
        await self.db.commit()

        verdict = await self.gateway.validate_ipn(val_id)

        # Re-load under a row lock before any state change. A duplicate IPN for
        # this same donation can arrive during the validate_ipn network call;
        # without the lock both would read PENDING and bump the campaign twice.
        # The lock serialises them — the loser sees COMPLETED and no-ops.
        donation = await self.repo.get_for_update(donation_id)
        if donation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Donation not found"
            )
        if donation.status == DonationStatus.COMPLETED.value:
            return donation  # a concurrent IPN already completed it — idempotent

        if not verdict.is_valid:
            if donation.status == DonationStatus.PENDING.value:
                donation.status = DonationStatus.FAILED.value
                await self.repo.commit()
                await self.repo.refresh(donation)
            logger.warning(
                "IPN validation failed for %s: status=%s", donation_id, verdict.status
            )
            return donation

        if donation.status != DonationStatus.PENDING.value:
            # A valid IPN arriving after the row went terminal (e.g. the stale
            # sweep failed it) means money may have moved — surface for review.
            logger.error(
                "Valid IPN for donation %s in terminal state %s — manual review",
                donation_id,
                donation.status,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Donation is not awaiting payment",
            )

        # Cross-check the validated figures against the pending row — STRICT, on
        # the most security-sensitive surface in the app. The validation API
        # always echoes tran_id and currency, so a missing field fails safe.
        if not (
            verdict.tran_id == tran_id
            and verdict.currency == "BDT"
            and verdict.gross == donation.gross_amount
            # Defence in depth: the gateway already folds store match into
            # is_valid, but re-assert it here at the money boundary. The
            # validator always echoes store_id for a real settlement, so a
            # populated-but-wrong store fails fast; a missing one falls through.
            and (
                not verdict.store_id or verdict.store_id == settings.sslcommerz_store_id
            )
        ):
            logger.error(
                "IPN mismatch for %s: tran=%s gross=%s cur=%s (row gross=%s)",
                donation_id,
                verdict.tran_id,
                verdict.gross,
                verdict.currency,
                donation.gross_amount,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="IPN does not match the pending donation",
            )

        # One transaction (row already locked): complete + bump + receipt number.
        now = datetime.now(timezone.utc)
        donation.status = DonationStatus.COMPLETED.value
        donation.fee_amount = verdict.fee
        donation.net_amount = verdict.net
        donation.gateway_val_id = val_id
        donation.gateway_bank_tran_id = verdict.bank_tran_id
        donation.gateway_payment_method = verdict.payment_method
        donation.completed_at = now
        year = now.astimezone(DHAKA_TZ).year
        seq = await self.repo.next_receipt_seq(year)
        donation.receipt_number = f"MK-{year}-{seq:06d}"
        new_raised: Decimal | None = None
        if donation.campaign_id is not None:
            new_raised = await self.repo.bump_campaign_raised(
                donation.campaign_id, donation.gross_amount
            )
        await self.repo.commit()
        await self.repo.refresh(donation)

        # The authoritative money event — log it at the moment it commits so a
        # completion is reconcilable from logs (gross/fee/net, gateway refs,
        # receipt number), not just inferred from a generic 200.
        logger.info(
            "donation completed",
            extra={
                "event": "donation_completed",
                "donation_id": str(donation.donation_id),
                "user_id": str(donation.user_id),
                "masjid_id": str(donation.masjid_id),
                "campaign_id": str(donation.campaign_id)
                if donation.campaign_id
                else None,
                "gross": str(donation.gross_amount),
                "fee": str(donation.fee_amount),
                "net": str(donation.net_amount),
                "val_id": val_id,
                "bank_tran_id": donation.gateway_bank_tran_id,
                "receipt_number": donation.receipt_number,
            },
        )

        await self._post_completion_effects(donation, new_raised=new_raised)
        return donation

    # ── Fail (→ FAILED) ────────────────────────────────────────────────────────

    async def fail(self, donation: Donation, reason: str = "") -> Donation:
        """PENDING → FAILED. No-op if already terminal."""
        if donation.status != DonationStatus.PENDING.value:
            return donation
        donation.status = DonationStatus.FAILED.value
        await self.repo.commit()
        await self.repo.refresh(donation)
        logger.info(
            "donation failed",
            extra={
                "event": "donation_failed",
                "donation_id": str(donation.donation_id),
                "masjid_id": str(donation.masjid_id),
                "gross": str(donation.gross_amount),
                "reason": reason or None,
            },
        )
        return donation

    # NOTE: a fail_from_redirect() path used to mark PENDING → FAILED from the
    # unauthenticated /payments/sslcommerz/redirect fail/cancel branch. It was
    # removed (CODEBASE_AUDIT #10): the redirect's donation_id is client-supplied
    # and not gateway-verified, so failing on it let anyone force another donor's
    # pending donation to FAILED by URL. Declined/cancelled payments are now
    # failed only by the authoritative IPN and the 24h stale-pending sweep.

    # ── Refund (→ REFUNDED, admin only) ──────────────────────────────────────

    async def refund(
        self,
        donation_id: uuid.UUID,
        reason: str = "Refund",
        *,
        actor: CurrentUser | None = None,
        ip_address: str | None = None,
    ) -> Donation:
        """COMPLETED → REFUNDED. Triggers the gateway refund (when a bank
        transaction id is on file), then in one transaction marks the donation
        refunded and reverses its campaign counter. The masjid balance may go
        negative by construction — already-disbursed funds must not block making
        a donor whole; the negative offsets future giving.

        The row is held under ``FOR UPDATE`` across the gateway call so two
        concurrent admin refunds can't both fire the reversal — the loser blocks,
        then sees REFUNDED and 409s. Refunds are a rare, admin-only action, so
        (unlike the IPN hot path) this brief lock-across-I/O is an acceptable
        trade for that serialisation. Gateway-first ordering means a gateway
        failure leaves the row COMPLETED (safe to retry); the residual window —
        gateway accepted but the commit then fails — is reconciled out of band.
        """
        # Lock the row so two concurrent admin refunds can't both fire the gateway
        # refund or double-reverse the campaign counter.
        donation = await self.repo.get_for_update(donation_id)
        if donation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Donation not found"
            )
        if donation.status != DonationStatus.COMPLETED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only a completed donation can be refunded",
            )

        if donation.gateway_bank_tran_id:
            result = await self.gateway.refund(
                bank_tran_id=donation.gateway_bank_tran_id,
                amount=donation.gross_amount,
                reason=reason,
            )
            if not result.success:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Gateway refund was not accepted ({result.status})",
                )

        now = datetime.now(timezone.utc)
        donation.status = DonationStatus.REFUNDED.value
        donation.refunded_at = now
        if donation.campaign_id is not None:
            await self.repo.bump_campaign_raised(
                donation.campaign_id, -donation.gross_amount
            )
        # Append-only audit trail for the money reversal (who/what/why), flushed
        # in the same transaction as the status flip so they commit atomically.
        if actor is not None:
            await self.audit.log(
                admin_id=actor.user_id,
                admin_email=actor.email,
                admin_role=actor.role,
                action="refund_donation",
                target_entity="donation",
                target_id=donation.donation_id,
                ip_address=ip_address,
                details={
                    "reason": reason,
                    "gross_amount": str(donation.gross_amount),
                    "masjid_id": str(donation.masjid_id),
                    "campaign_id": str(donation.campaign_id)
                    if donation.campaign_id
                    else None,
                },
            )
        await self.repo.commit()
        await self.repo.refresh(donation)
        logger.info(
            "donation refunded",
            extra={
                "event": "donation_refunded",
                "donation_id": str(donation.donation_id),
                "masjid_id": str(donation.masjid_id),
                "gross": str(donation.gross_amount),
                "reason": reason,
                "actor_id": str(actor.user_id) if actor else None,
            },
        )
        return donation

    # ── Balance & disbursements (derived ledger) ──────────────────────────────

    async def balance_of(self, masjid_id: uuid.UUID) -> Decimal:
        """Derived balance: SUM(net completed donations) − SUM(disbursements).
        Never stored; negative is allowed by construction (post-disbursement
        refunds)."""
        net = await self.repo.sum_net_completed(masjid_id)
        out = await self.disbursement_repo.sum_for_masjid(masjid_id)
        return (net - out).quantize(_TWO_PLACES)

    async def record_disbursement(
        self,
        *,
        masjid_id: uuid.UUID,
        amount: Decimal,
        method: str,
        disbursed_on,
        recorded_by_id: uuid.UUID,
        reference: str | None = None,
        notes: str | None = None,
        actor: CurrentUser | None = None,
        ip_address: str | None = None,
    ) -> Disbursement:
        """Record a manual payout against a masjid's balance (no payout API).

        This is money LEAVING the platform, so it is both structured-logged and
        written to the append-only audit trail (when an admin actor is supplied).
        """
        amount = Decimal(amount).quantize(_TWO_PLACES)
        if amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Disbursement amount must be positive",
            )
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if masjid is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Masjid not found"
            )
        disbursement = Disbursement(
            masjid_id=masjid_id,
            amount=amount,
            method=method,
            reference=reference,
            disbursed_on=disbursed_on,
            recorded_by_id=recorded_by_id,
            notes=notes,
        )
        await self.disbursement_repo.add(disbursement)  # flush → disbursement_id
        if actor is not None:
            await self.audit.log(
                admin_id=actor.user_id,
                admin_email=actor.email,
                admin_role=actor.role,
                action="record_disbursement",
                target_entity="disbursement",
                target_id=disbursement.disbursement_id,
                ip_address=ip_address,
                details={
                    "masjid_id": str(masjid_id),
                    "amount": str(amount),
                    "method": method,
                    "reference": reference,
                },
            )
        await self.disbursement_repo.commit()
        await self.disbursement_repo.refresh(disbursement)
        logger.info(
            "disbursement recorded",
            extra={
                "event": "disbursement_recorded",
                "disbursement_id": str(disbursement.disbursement_id),
                "masjid_id": str(masjid_id),
                "amount": str(amount),
                "method": method,
                "actor_id": str(actor.user_id) if actor else str(recorded_by_id),
            },
        )
        return disbursement

    # ── Dashboard reads (donor-facing) ────────────────────────────────────────

    async def get_owned_status(
        self, donation_id: uuid.UUID, user: CurrentUser
    ) -> DonationStatusResponse:
        """The status-poll target. Owner-only — a non-owner gets 404, never a
        leak that the donation exists."""
        donation = await self.repo.get_by_id(donation_id)
        if donation is None or donation.user_id != user.user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Donation not found"
            )
        return _to_status_response(donation)

    async def get_history(
        self,
        user: CurrentUser,
        *,
        masjid_id: uuid.UUID | None,
        category: str | None,
        status_: str | None,
        year: int | None,
        cursor: str | None,
        limit: int = 20,
    ) -> DonationHistoryResponse:
        before = _decode_history_cursor(cursor) if cursor else None
        # Over-fetch by one to detect whether a further page exists, so the last
        # full page does not emit a phantom next_cursor pointing at an empty page.
        rows = await self.repo.list_for_user(
            user.user_id,
            masjid_id=masjid_id,
            category=category,
            status_=status_,
            year=year,
            limit=limit + 1,
            before=before,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            DonationHistoryItem(
                donation_id=d.donation_id,
                masjid_id=d.masjid_id,
                masjid_name=name,
                campaign_id=d.campaign_id,
                category=d.category,
                gross_amount=d.gross_amount,
                status=d.status,
                is_anonymous=d.is_anonymous,
                receipt_number=d.receipt_number,
                created_at=d.created_at,
                completed_at=d.completed_at,
            )
            for d, name in rows
        ]
        next_cursor = None
        if has_more and rows:
            last_donation = rows[-1][0]
            next_cursor = _encode_history_cursor(
                last_donation.created_at, last_donation.donation_id
            )
        return DonationHistoryResponse(items=items, next_cursor=next_cursor)

    async def get_summary(self, user: CurrentUser) -> DonationSummaryResponse:
        year = datetime.now(DHAKA_TZ).year
        lifetime, year_total, per_masjid = await self.repo.summary_for_user(
            user.user_id, this_year=year
        )
        return DonationSummaryResponse(
            lifetime_total=lifetime,
            this_year_total=year_total,
            year=year,
            per_masjid=[
                PerMasjidTotal(masjid_id=mid, masjid_name=name, total=total)
                for mid, name, total in per_masjid
            ],
        )

    # ── Admin reads (masjid-scoped; anonymity mask in the query/service layer) ──

    def _check_masjid_scope(self, user: CurrentUser, masjid_id: uuid.UUID) -> None:
        """A masjid_admin may act only on its own masjid; platform_admin on any."""
        if user.is_platform_admin:
            return
        if user.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to your own masjid",
            )

    async def list_masjid_donations(
        self,
        user: CurrentUser,
        masjid_id: uuid.UUID,
        *,
        status_: str | None,
        page: int,
        page_size: int,
    ) -> AdminDonationListResponse:
        self._check_masjid_scope(user, masjid_id)
        rows, total = await self.repo.list_for_masjid(
            masjid_id,
            status_=status_,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        items = [
            AdminDonationItem(
                donation_id=d.donation_id,
                # Anonymity mask applied here, not in the client.
                donor_label="Anonymous donor"
                if d.is_anonymous
                else (d.donor_name or "Donor"),
                category=d.category,
                gross_amount=d.gross_amount,
                net_amount=d.net_amount,
                status=d.status,
                created_at=d.created_at,
                completed_at=d.completed_at,
            )
            for d in rows
        ]
        return AdminDonationListResponse(
            items=items, total=total, page=page, page_size=page_size
        )

    async def get_masjid_balance(
        self, user: CurrentUser, masjid_id: uuid.UUID
    ) -> BalanceResponse:
        self._check_masjid_scope(user, masjid_id)
        net = await self.repo.sum_net_completed(masjid_id)
        out = await self.disbursement_repo.sum_for_masjid(masjid_id)
        return BalanceResponse(
            masjid_id=masjid_id,
            net_donations=net,
            total_disbursed=out,
            balance=(net - out).quantize(_TWO_PLACES),
        )

    async def all_balances(self) -> BalanceListResponse:
        """Platform-wide per-masjid balances (platform_admin) — drives
        disbursement runs from data, not requests."""
        net_map = await self.repo.net_by_masjid()
        out_map = await self.disbursement_repo.total_by_masjid()
        masjid_ids = set(net_map) | set(out_map)
        # One query for all names (was an N+1 get_by_id per masjid).
        names = await self.masjid_repo.names_by_ids(list(masjid_ids))
        items = [
            MasjidBalanceItem(
                masjid_id=mid,
                masjid_name=names.get(mid, "(unknown)"),
                net_donations=net_map.get(mid, Decimal("0.00")),
                total_disbursed=out_map.get(mid, Decimal("0.00")),
                balance=(
                    net_map.get(mid, Decimal("0.00"))
                    - out_map.get(mid, Decimal("0.00"))
                ).quantize(_TWO_PLACES),
            )
            for mid in masjid_ids
        ]
        items.sort(key=lambda i: i.balance, reverse=True)
        return BalanceListResponse(items=items)

    async def refund_by_id(
        self,
        donation_id: uuid.UUID,
        reason: str,
        *,
        actor: CurrentUser | None = None,
        ip_address: str | None = None,
    ) -> DonationStatusResponse:
        # refund() does the locked read + 404/409 itself; a pre-read here would
        # open a transaction held across the gateway call for nothing.
        donation = await self.refund(
            donation_id, reason, actor=actor, ip_address=ip_address
        )
        return _to_status_response(donation)

    # ── Post-commit side effects (best-effort) ────────────────────────────────

    async def _post_completion_effects(
        self, donation: Donation, *, new_raised: Decimal | None = None
    ) -> None:
        """Email + push + badge re-eval, after the completion has committed.

        Every effect is best-effort and isolated: a side-effect failure must
        never undo a completed, money-moving donation.
        """
        # Each effect is isolated AND the shared session is reset on failure: a
        # raised effect leaves the session in an aborted-transaction state, so we
        # roll back in the handler. Without it the *next* effect would fail
        # spuriously with PendingRollbackError and a single hiccup would silently
        # disable the rest of the fan-out.
        try:
            await self._send_confirmation(donation)
        except Exception:
            logger.exception(
                "donation %s: confirmation notify failed", donation.donation_id
            )
            await self.db.rollback()
        try:
            # Import here to avoid an import cycle (gamification imports the
            # donation repository for the giving-months counter).
            from app.services.gamification_service import GamificationService

            await GamificationService(self.db).reevaluate_badges(donation.user_id)
        except Exception:
            logger.exception("donation %s: badge re-eval failed", donation.donation_id)
            await self.db.rollback()
        try:
            await self._maybe_campaign_milestone(donation, new_raised=new_raised)
        except Exception:
            logger.exception("donation %s: milestone push failed", donation.donation_id)
            await self.db.rollback()

    async def _maybe_campaign_milestone(
        self, donation: Donation, *, new_raised: Decimal | None = None
    ) -> None:
        """Fire CAMPAIGN_MILESTONE to every donor of the campaign, exactly once —
        on the completion that pushed raised_amount across the target.

        The crossing is judged from ``new_raised`` — the raised_amount produced by
        THIS increment's atomic RETURNING — not a fresh post-commit read. Under
        concurrent completions a re-read could reflect another transaction's bump,
        which would let two completions both see (or both miss) the crossing; the
        per-increment value makes "the completion that crossed the target" exact.
        """
        if donation.campaign_id is None or new_raised is None:
            return
        campaign = await self.campaign_repo.get_by_id(donation.campaign_id)
        if campaign is None:
            return
        crossed_now = (
            new_raised >= campaign.target_amount
            and (new_raised - donation.gross_amount) < campaign.target_amount
        )
        if not crossed_now:
            return
        from app.services.push_service import PushMessage, PushService

        donor_ids = await self.repo.campaign_donor_ids(campaign.campaign_id)
        if not donor_ids:
            return
        await PushService(self.db).notify_users(
            donor_ids,
            PushMessage(
                message_type=PushMessageType.CAMPAIGN_MILESTONE,
                title="Campaign fully funded 🎉",
                body=f'"{campaign.title}" reached its goal. JazakAllah khairan.',
                data={
                    "campaign_id": str(campaign.campaign_id),
                    "masjid_id": str(campaign.masjid_id),
                },
            ),
        )

    # ── Stale-pending sweep (called by the scheduler) ─────────────────────────

    async def sweep_stale_pending(
        self, now: datetime | None = None, older_than_hours: int = 24
    ) -> int:
        """PENDING donations older than the cutoff → FAILED + one recovery push.
        Once FAILED they are never re-selected, so the recovery push never
        repeats. Returns the number swept. Push is best-effort."""
        from app.services.push_service import PushMessage, PushService

        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=older_than_hours)
        stale = await self.repo.list_stale_pending(cutoff)
        if not stale:
            return 0
        for d in stale:
            d.status = DonationStatus.FAILED.value
        await self.repo.commit()

        push = PushService(self.db)
        for d in stale:
            try:
                await push.notify_users(
                    [d.user_id],
                    PushMessage(
                        message_type=PushMessageType.PAYMENT_RECOVERY,
                        title="Complete your donation?",
                        body=(
                            f"Your ৳{d.gross_amount:.0f} donation wasn't finished. "
                            f"Tap to complete it."
                        ),
                        data={
                            "donation_id": str(d.donation_id),
                            "masjid_id": str(d.masjid_id),
                        },
                    ),
                )
            except Exception:
                logger.exception("recovery push failed for %s", d.donation_id)
        logger.info("Stale-pending donations swept to FAILED: %d", len(stale))
        return len(stale)

    async def _send_confirmation(self, donation: Donation) -> None:
        from app.services.email_service import send_email
        from app.services.push_service import PushMessage, PushService

        gross = f"{donation.gross_amount:.0f}"
        await PushService(self.db).notify_users(
            [donation.user_id],
            PushMessage(
                message_type=PushMessageType.DONATION_CONFIRMED,
                title="Donation received",
                body=f"Your ৳{gross} donation has been confirmed. JazakAllah khairan.",
                data={
                    "donation_id": str(donation.donation_id),
                    "masjid_id": str(donation.masjid_id),
                },
            ),
        )
        if donation.donor_email:
            await send_email(
                to=donation.donor_email,
                subject="Your MasjidKoi donation receipt",
                body=(
                    f"Assalamu alaikum,\n\n"
                    f"We have received your donation of ৳{gross} "
                    f"(receipt {donation.receipt_number}).\n"
                    f"JazakAllah khairan for your generosity.\n\n"
                    f"— MasjidKoi"
                ),
            )


def _to_status_response(donation: Donation) -> DonationStatusResponse:
    return DonationStatusResponse(
        donation_id=donation.donation_id,
        status=donation.status,
        category=donation.category,
        masjid_id=donation.masjid_id,
        campaign_id=donation.campaign_id,
        gross_amount=donation.gross_amount,
        fee_amount=donation.fee_amount,
        net_amount=donation.net_amount,
        is_anonymous=donation.is_anonymous,
        receipt_number=donation.receipt_number,
        gateway_payment_method=donation.gateway_payment_method,
        completed_at=donation.completed_at,
        created_at=donation.created_at,
    )
