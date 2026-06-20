"""Smoke tests for ReceiptService (PRD 05).

Per the PRD, receipt *content* is deliberately not asserted (rendering churn
makes it brittle). These check only the security gate (completed + owner) and
that a PDF actually comes out — not what's in it.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.donation import Donation
from app.models.enums import DonationStatus
from app.services.receipt_service import ReceiptService
from tests.test_donation_ledger import _user

pytestmark = pytest.mark.asyncio


async def _completed_donation(db, uid, masjid_id) -> Donation:
    d = Donation(
        user_id=uid,
        masjid_id=masjid_id,
        category="general",
        status=DonationStatus.COMPLETED.value,
        gross_amount=Decimal("500.00"),
        fee_amount=Decimal("12.25"),
        net_amount=Decimal("487.75"),
        is_anonymous=False,
        donor_name="Aisha R.",
        receipt_number="MK-2026-000001",
        completed_at=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
    )
    db.add(d)
    await db.flush()
    return d


async def test_receipt_requires_completed_donation(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    pending = Donation(
        user_id=uid,
        masjid_id=masjid.masjid_id,
        category="general",
        status=DonationStatus.PENDING.value,
        gross_amount=Decimal("500.00"),
        fee_amount=Decimal("0.00"),
        net_amount=Decimal("500.00"),
        is_anonymous=False,
    )
    db.add(pending)
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await ReceiptService(db).donation_receipt(pending.donation_id, _user(uid))
    assert exc.value.status_code == 400


async def test_receipt_owner_only(db, seed):
    owner = await seed.user()
    other = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    d = await _completed_donation(db, owner, masjid.masjid_id)

    with pytest.raises(HTTPException) as exc:
        await ReceiptService(db).donation_receipt(d.donation_id, _user(other))
    assert exc.value.status_code == 404


async def test_receipt_pdf_is_produced(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    d = await _completed_donation(db, uid, masjid.masjid_id)

    pdf = await ReceiptService(db).donation_receipt(d.donation_id, _user(uid))
    assert pdf[:5] == b"%PDF-"


async def test_annual_summary_pdf_is_produced(db, seed):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    await _completed_donation(db, uid, masjid.masjid_id)

    pdf = await ReceiptService(db).annual_summary(_user(uid), 2026)
    assert pdf[:5] == b"%PDF-"
