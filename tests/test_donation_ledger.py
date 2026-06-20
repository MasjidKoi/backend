"""Tests for DonationLedger — the money-correctness core (PRD 05).

These assert external behaviour through the service's public interface: the
states it moves donations through, the balances and campaign counters it
maintains, and the verdicts it acts on. The SSLCommerz boundary is faked at the
adapter seam (``FakeGateway``), never monkeypatched mid-module.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import CurrentUser
from app.models.donation import Donation
from app.models.enums import AdminRole, AuthAssuranceLevel, BadgeType, DonationStatus
from app.models.masjid_campaign import MasjidCampaign
from app.models.user_badge import UserBadge
from app.services.donation_service import DonationService
from app.services.sslcommerz_gateway import (
    RefundResult,
    SessionResult,
    ValidationResult,
)

pytestmark = pytest.mark.asyncio


def _user(user_id: uuid.UUID) -> CurrentUser:
    return CurrentUser(
        user_id=user_id,
        email="donor@test.local",
        role=AdminRole.APP_USER,
        aal=AuthAssuranceLevel.AAL1,
    )


class FakeGateway:
    """Fakes the SslcommerzGateway public interface for ledger tests.

    create_session captures the tran_id/amount; validate_ipn returns a verdict
    echoing them so the happy path is self-consistent. Pass ``override`` to force
    a specific verdict (mismatch / invalid), or ``fail_session`` to simulate a
    session-create failure.
    """

    def __init__(
        self,
        *,
        fee: Decimal = Decimal("12.25"),
        verdict_status: str = "VALID",
        fail_session: bool = False,
        override: ValidationResult | None = None,
    ) -> None:
        self.fee = fee
        self.verdict_status = verdict_status
        self.fail_session = fail_session
        self.override = override
        self.captured: dict = {}
        self.validate_calls = 0

    async def create_session(self, *, tran_id, amount, **kw) -> SessionResult:
        if self.fail_session:
            raise HTTPException(status_code=502, detail="gateway down")
        self.captured = {"tran_id": tran_id, "amount": amount}
        return SessionResult(
            gateway_url=f"https://gw/pay/{tran_id}", session_key="sk-1"
        )

    async def validate_ipn(self, val_id) -> ValidationResult:
        self.validate_calls += 1
        if self.override is not None:
            return self.override
        gross = self.captured["amount"]
        net = (gross - self.fee).quantize(Decimal("0.01"))
        return ValidationResult(
            is_valid=self.verdict_status in {"VALID", "VALIDATED"},
            status=self.verdict_status,
            tran_id=self.captured["tran_id"],
            gross=gross,
            net=net,
            fee=self.fee,
            currency="BDT",
            store_id="",
            bank_tran_id="BANK-1",
            payment_method="BKASH-bKash",
            raw={},
        )

    async def refund(self, *, bank_tran_id, amount, reason) -> RefundResult:
        return RefundResult(success=True, status="success", refund_ref_id="ref-1")


# ── create_pending ────────────────────────────────────────────────────────────


async def test_create_pending_general_starts_pending(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = DonationService(db, gateway=FakeGateway())

    result = await svc.create_pending(
        _user(uid),
        masjid_id=masjid.masjid_id,
        amount=Decimal("500"),
        category="general",
        is_anonymous=False,
    )

    d = result.donation
    assert result.gateway_url.startswith("https://gw/pay/")
    assert d.status == DonationStatus.PENDING.value
    assert d.gross_amount == Decimal("500.00")
    assert d.fee_amount == Decimal("0.00")
    assert d.net_amount == Decimal("500.00")  # net = gross until the IPN
    assert d.gateway_session_key == "sk-1"


async def test_create_pending_rejects_out_of_bounds_amount(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = DonationService(db, gateway=FakeGateway())

    with pytest.raises(HTTPException) as exc:
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid.masjid_id,
            amount=Decimal("5"),
            category="general",
            is_anonymous=False,
        )
    assert exc.value.status_code == 422


async def test_create_pending_rejects_donations_disabled(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=False)
    svc = DonationService(db, gateway=FakeGateway())

    with pytest.raises(HTTPException) as exc:
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid.masjid_id,
            amount=Decimal("100"),
            category="general",
            is_anonymous=False,
        )
    assert exc.value.status_code == 403


async def test_create_pending_campaign_derives_masjid_and_category(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    campaign = await seed.campaign(masjid.masjid_id)
    svc = DonationService(db, gateway=FakeGateway())

    result = await svc.create_pending(
        _user(uid),
        masjid_id=uuid.uuid4(),  # bogus — must be ignored, derived from campaign
        amount=Decimal("250"),
        category="general",  # must be forced to campaign
        is_anonymous=False,
        campaign_id=campaign.campaign_id,
    )
    assert result.donation.masjid_id == masjid.masjid_id
    assert result.donation.category == "campaign"
    assert result.donation.campaign_id == campaign.campaign_id


async def test_create_pending_inactive_campaign_rejected(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    campaign = await seed.campaign(masjid.masjid_id, status="Completed")
    svc = DonationService(db, gateway=FakeGateway())

    with pytest.raises(HTTPException) as exc:
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid.masjid_id,
            amount=Decimal("250"),
            category="general",
            is_anonymous=False,
            campaign_id=campaign.campaign_id,
        )
    assert exc.value.status_code == 400


async def test_create_pending_session_failure_marks_failed(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = DonationService(db, gateway=FakeGateway(fail_session=True))

    with pytest.raises(HTTPException):
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid.masjid_id,
            amount=Decimal("100"),
            category="general",
            is_anonymous=False,
        )
    # The donation row exists and is FAILED (it never had a payable URL).
    rows = (
        (await db.execute(select(Donation).where(Donation.user_id == uid)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == DonationStatus.FAILED.value


# ── complete_from_ipn ────────────────────────────────────────────────────────


async def test_complete_from_ipn_happy_path(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    gw = FakeGateway(fee=Decimal("12.25"))
    svc = DonationService(db, gateway=gw)

    created = (
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid.masjid_id,
            amount=Decimal("500"),
            category="general",
            is_anonymous=False,
        )
    ).donation

    done = await svc.complete_from_ipn(val_id="VAL-1", tran_id=str(created.donation_id))

    assert done.status == DonationStatus.COMPLETED.value
    assert done.fee_amount == Decimal("12.25")
    assert done.net_amount == Decimal("487.75")  # gross − fee
    assert done.gross_amount == Decimal("500.00")
    assert done.gateway_val_id == "VAL-1"
    assert done.gateway_payment_method == "BKASH-bKash"
    assert done.completed_at is not None
    assert done.receipt_number and done.receipt_number.startswith("MK-")


async def test_complete_from_ipn_bumps_campaign_raised(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    campaign = await seed.campaign(masjid.masjid_id)
    svc = DonationService(db, gateway=FakeGateway())

    created = (
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid.masjid_id,
            amount=Decimal("500"),
            category="general",
            is_anonymous=False,
            campaign_id=campaign.campaign_id,
        )
    ).donation
    await svc.complete_from_ipn(val_id="VAL-1", tran_id=str(created.donation_id))

    refreshed = (
        await db.execute(
            select(MasjidCampaign).where(
                MasjidCampaign.campaign_id == campaign.campaign_id
            )
        )
    ).scalar_one()
    # Campaign bar moves by GROSS, like the donor's receipt.
    assert refreshed.raised_amount == Decimal("500.00")


async def test_complete_from_ipn_is_idempotent(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    campaign = await seed.campaign(masjid.masjid_id)
    svc = DonationService(db, gateway=FakeGateway())

    created = (
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid.masjid_id,
            amount=Decimal("500"),
            category="general",
            is_anonymous=False,
            campaign_id=campaign.campaign_id,
        )
    ).donation

    first = await svc.complete_from_ipn(
        val_id="VAL-1", tran_id=str(created.donation_id)
    )
    receipt = first.receipt_number
    # Replay the same IPN twice more.
    await svc.complete_from_ipn(val_id="VAL-1", tran_id=str(created.donation_id))
    again = await svc.complete_from_ipn(
        val_id="VAL-1", tran_id=str(created.donation_id)
    )

    assert again.status == DonationStatus.COMPLETED.value
    assert again.receipt_number == receipt  # no new receipt number burned

    refreshed = (
        await db.execute(
            select(MasjidCampaign).where(
                MasjidCampaign.campaign_id == campaign.campaign_id
            )
        )
    ).scalar_one()
    # Campaign bumped exactly once despite three IPNs.
    assert refreshed.raised_amount == Decimal("500.00")


async def test_complete_from_ipn_invalid_verdict_fails(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = DonationService(db, gateway=FakeGateway(verdict_status="INVALID_TRANSACTION"))

    created = (
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid.masjid_id,
            amount=Decimal("500"),
            category="general",
            is_anonymous=False,
        )
    ).donation
    done = await svc.complete_from_ipn(val_id="VAL-1", tran_id=str(created.donation_id))
    assert done.status == DonationStatus.FAILED.value


async def test_complete_from_ipn_validator_unreachable_stays_pending(db, seed):
    """A transient validator error is retryable, NOT a 'failed' verdict: the
    donation must stay PENDING so a later IPN retry can complete a paid donation
    (regression for the silent money-loss bug where a validator 5xx → FAILED)."""
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)

    class _UnreachableGateway(FakeGateway):
        async def validate_ipn(self, val_id) -> ValidationResult:
            raise HTTPException(status_code=502, detail="validator down")

    svc = DonationService(db, gateway=_UnreachableGateway())
    created = (
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid.masjid_id,
            amount=Decimal("500"),
            category="general",
            is_anonymous=False,
        )
    ).donation

    with pytest.raises(HTTPException) as exc:
        await svc.complete_from_ipn(val_id="VAL-1", tran_id=str(created.donation_id))
    assert exc.value.status_code == 502

    row = (
        await db.execute(
            select(Donation).where(Donation.donation_id == created.donation_id)
        )
    ).scalar_one()
    assert row.status == DonationStatus.PENDING.value  # never FAILED on a blip


async def test_complete_from_ipn_amount_mismatch_rejected(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = DonationService(db, gateway=FakeGateway())
    created = (
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid.masjid_id,
            amount=Decimal("500"),
            category="general",
            is_anonymous=False,
        )
    ).donation

    # Force a valid-looking verdict whose gross does NOT match the pending row.
    svc.gateway.override = ValidationResult(
        is_valid=True,
        status="VALID",
        tran_id=str(created.donation_id),
        gross=Decimal("999.00"),
        net=Decimal("980.00"),
        fee=Decimal("19.00"),
        currency="BDT",
        store_id="",
        bank_tran_id="BANK-1",
        payment_method="card",
        raw={},
    )
    with pytest.raises(HTTPException) as exc:
        await svc.complete_from_ipn(val_id="VAL-9", tran_id=str(created.donation_id))
    assert exc.value.status_code == 400

    still = (
        await db.execute(
            select(Donation).where(Donation.donation_id == created.donation_id)
        )
    ).scalar_one()
    assert still.status == DonationStatus.PENDING.value  # unchanged


async def test_complete_from_ipn_currency_mismatch_rejected(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = DonationService(db, gateway=FakeGateway())
    created = (
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid.masjid_id,
            amount=Decimal("500"),
            category="general",
            is_anonymous=False,
        )
    ).donation

    # A VALID verdict for the right tran/amount but the WRONG currency must be
    # rejected (strict match), leaving the donation PENDING.
    svc.gateway.override = ValidationResult(
        is_valid=True,
        status="VALID",
        tran_id=str(created.donation_id),
        gross=Decimal("500.00"),
        net=Decimal("487.75"),
        fee=Decimal("12.25"),
        currency="USD",
        store_id="",
        bank_tran_id="BANK-1",
        payment_method="card",
        raw={},
    )
    with pytest.raises(HTTPException) as exc:
        await svc.complete_from_ipn(val_id="VAL-9", tran_id=str(created.donation_id))
    assert exc.value.status_code == 400

    still = (
        await db.execute(
            select(Donation).where(Donation.donation_id == created.donation_id)
        )
    ).scalar_one()
    assert still.status == DonationStatus.PENDING.value


async def test_complete_from_ipn_unknown_tran_404(db, seed):
    svc = DonationService(db, gateway=FakeGateway())
    with pytest.raises(HTTPException) as exc:
        await svc.complete_from_ipn(val_id="VAL-1", tran_id=str(uuid.uuid4()))
    assert exc.value.status_code == 404


# ── fail ────────────────────────────────────────────────────────────────────


async def test_fail_pending_then_terminal_noop(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = DonationService(db, gateway=FakeGateway())
    created = (
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid.masjid_id,
            amount=Decimal("100"),
            category="general",
            is_anonymous=False,
        )
    ).donation

    failed = await svc.fail(created, reason="stale")
    assert failed.status == DonationStatus.FAILED.value
    # Failing an already-terminal donation is a no-op (stays FAILED).
    again = await svc.fail(failed)
    assert again.status == DonationStatus.FAILED.value


# ── Generous Giver badge activation (PRD 08 wiring) ──────────────────────────


async def test_completed_donations_activate_generous_giver(db, seed):
    """Three consecutive Dhaka months with a completed donation award tier 1."""
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)

    # Insert completed donations directly across April/May/June 2026.
    for month in (4, 5, 6):
        db.add(
            Donation(
                user_id=uid,
                masjid_id=masjid.masjid_id,
                category="general",
                status=DonationStatus.COMPLETED.value,
                gross_amount=Decimal("100.00"),
                fee_amount=Decimal("0.00"),
                net_amount=Decimal("100.00"),
                is_anonymous=False,
                completed_at=datetime(2026, month, 15, 12, 0, tzinfo=timezone.utc),
            )
        )
    await db.flush()

    from app.services.gamification_service import GamificationService

    await GamificationService(db).reevaluate_badges(uid)

    badges = (
        (await db.execute(select(UserBadge).where(UserBadge.user_id == uid)))
        .scalars()
        .all()
    )
    giver = [b for b in badges if b.badge_type == BadgeType.GENEROUS_GIVER.value]
    assert giver, "Generous Giver tier should be awarded after 3 consecutive months"
    assert min(b.tier for b in giver) == 1
