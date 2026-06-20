"""Tests for refund, disbursements, and the derived balance (PRD 05).

Balance is SUM(net completed donations) − SUM(disbursements), never stored, and
may go negative after a post-disbursement refund. Refund reverses the campaign
counter. The SSLCommerz boundary is faked via FakeGateway (reused from the
ledger tests).
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.enums import DonationStatus
from app.models.masjid_campaign import MasjidCampaign
from app.services.donation_service import DonationService
from tests.test_donation_ledger import FakeGateway, _user

pytestmark = pytest.mark.asyncio


async def _complete_donation(db, svc, uid, masjid_id, amount, campaign_id=None):
    created = (
        await svc.create_pending(
            _user(uid),
            masjid_id=masjid_id,
            amount=Decimal(amount),
            category="general",
            is_anonymous=False,
            campaign_id=campaign_id,
        )
    ).donation
    return await svc.complete_from_ipn(
        val_id=f"VAL-{created.donation_id}", tran_id=str(created.donation_id)
    )


# ── Balance ──────────────────────────────────────────────────────────────────


async def test_balance_is_net_completed_minus_disbursements(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = DonationService(db, gateway=FakeGateway(fee=Decimal("12.25")))

    await _complete_donation(db, svc, uid, masjid.masjid_id, "500")  # net 487.75
    await _complete_donation(db, svc, uid, masjid.masjid_id, "500")  # net 487.75
    # Two completed donations → net 975.50.
    assert await svc.balance_of(masjid.masjid_id) == Decimal("975.50")

    await svc.record_disbursement(
        masjid_id=masjid.masjid_id,
        amount=Decimal("400"),
        method="bank",
        disbursed_on=date(2026, 6, 1),
        recorded_by_id=uuid.uuid4(),
        reference="TXN-1",
    )
    assert await svc.balance_of(masjid.masjid_id) == Decimal("575.50")


async def test_pending_donations_excluded_from_balance(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = DonationService(db, gateway=FakeGateway())
    # A pending (never-completed) donation contributes nothing to the balance.
    await svc.create_pending(
        _user(uid),
        masjid_id=masjid.masjid_id,
        amount=Decimal("500"),
        category="general",
        is_anonymous=False,
    )
    assert await svc.balance_of(masjid.masjid_id) == Decimal("0.00")


async def test_record_disbursement_rejects_nonpositive(db, seed):
    masjid = await seed.masjid(donations_enabled=True)
    svc = DonationService(db, gateway=FakeGateway())
    with pytest.raises(HTTPException) as exc:
        await svc.record_disbursement(
            masjid_id=masjid.masjid_id,
            amount=Decimal("0"),
            method="cash",
            disbursed_on=date(2026, 6, 1),
            recorded_by_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 422


# ── Refund ────────────────────────────────────────────────────────────────


async def test_refund_reverses_campaign_and_balance(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    campaign = await seed.campaign(masjid.masjid_id)
    svc = DonationService(db, gateway=FakeGateway())

    done = await _complete_donation(
        db, svc, uid, masjid.masjid_id, "500", campaign_id=campaign.campaign_id
    )
    # Campaign moved + balance credited.
    bumped = (
        await db.execute(
            select(MasjidCampaign).where(
                MasjidCampaign.campaign_id == campaign.campaign_id
            )
        )
    ).scalar_one()
    assert bumped.raised_amount == Decimal("500.00")

    refunded = await svc.refund(done, reason="duplicate")
    assert refunded.status == DonationStatus.REFUNDED.value
    assert refunded.refunded_at is not None

    reversed_campaign = (
        await db.execute(
            select(MasjidCampaign).where(
                MasjidCampaign.campaign_id == campaign.campaign_id
            )
        )
    ).scalar_one()
    assert reversed_campaign.raised_amount == Decimal("0.00")  # reversed
    # Refunded donation no longer counts toward the balance.
    assert await svc.balance_of(masjid.masjid_id) == Decimal("0.00")


async def test_refund_can_drive_balance_negative(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    svc = DonationService(db, gateway=FakeGateway(fee=Decimal("12.25")))

    done = await _complete_donation(db, svc, uid, masjid.masjid_id, "500")  # net 487.75
    # NGO disburses the whole net out of band...
    await svc.record_disbursement(
        masjid_id=masjid.masjid_id,
        amount=Decimal("487.75"),
        method="bank",
        disbursed_on=date(2026, 6, 1),
        recorded_by_id=uuid.uuid4(),
    )
    assert await svc.balance_of(masjid.masjid_id) == Decimal("0.00")

    # ...then a refund drives the balance negative (offsets future giving).
    await svc.refund(done, reason="chargeback")
    assert await svc.balance_of(masjid.masjid_id) == Decimal("-487.75")


async def test_refund_only_from_completed(db, seed):
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
    with pytest.raises(HTTPException) as exc:
        await svc.refund(created)
    assert exc.value.status_code == 409
