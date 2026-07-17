"""HTTP smoke tests for the donation surfaces (PRD 05).

The PRD scopes deep testing to the money path (the ledger/gateway/recurring
unit tests). These are light wiring checks for the security-sensitive IPN
endpoint and the gateway redirect — they touch no real gateway and no network.
"""

import uuid
from decimal import Decimal

import pytest

from app.models.donation import Donation
from app.models.enums import DonationStatus
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def _pending_donation(
    db, uid, masjid_id, *, status=DonationStatus.PENDING
) -> Donation:
    d = Donation(
        user_id=uid,
        masjid_id=masjid_id,
        category="general",
        status=status.value,
        gross_amount=Decimal("500.00"),
        fee_amount=Decimal("0.00"),
        net_amount=Decimal("500.00"),
        is_anonymous=False,
    )
    db.add(d)
    await db.flush()
    return d


async def test_ipn_unknown_tran_returns_200_rejected(client):
    # Unknown tran_id → service 404s before any gateway call → handled, no retry.
    r = await client.post(
        "/payments/sslcommerz/ipn",
        data={"val_id": "VAL-X", "tran_id": str(uuid.uuid4())},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


async def test_ipn_missing_fields_ignored(client):
    r = await client.post("/payments/sslcommerz/ipn", data={})
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


async def test_redirect_deep_links_into_app(client):
    did = str(uuid.uuid4())
    r = await client.get(
        f"/payments/sslcommerz/redirect/success?donation_id={did}",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"masjidkoi://donation/{did}?status=success"


async def test_redirect_accepts_post(client):
    # SSLCommerz redirects to the success URL by POST — a GET-only route 405s it.
    # (No val_id in the body, so the completion path is skipped: just navigation.)
    did = str(uuid.uuid4())
    r = await client.post(
        f"/payments/sslcommerz/redirect/success?donation_id={did}",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"masjidkoi://donation/{did}?status=success"


async def test_redirect_unknown_outcome_falls_back_to_fail(client):
    did = str(uuid.uuid4())
    r = await client.get(
        f"/payments/sslcommerz/redirect/bogus?donation_id={did}",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("status=fail")


@pytest.mark.parametrize("outcome", ["fail", "cancel"])
async def test_redirect_fail_cancel_does_not_write(client, db, seed, outcome):
    # CODEBASE_AUDIT #10: the redirect is unauthenticated and the donation_id is
    # client-supplied and NOT gateway-verified, so a fail/cancel redirect must NOT
    # transition the row — otherwise anyone could force another donor's PENDING
    # donation to FAILED by URL. It still renders the deep-link; the authoritative
    # IPN and the 24h stale-pending sweep own the actual failure transition.
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    pending = await _pending_donation(db, uid, masjid.masjid_id)
    await seed.commit()

    r = await client.post(
        f"/payments/sslcommerz/redirect/{outcome}?donation_id={pending.donation_id}",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith(f"status={outcome}")

    await db.refresh(pending)
    assert pending.status == DonationStatus.PENDING.value


async def test_redirect_fail_does_not_clobber_completed(client, db, seed):
    # A valid IPN may complete the row before the fail/cancel redirect lands;
    # the redirect must never overwrite a terminal state.
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    completed = await _pending_donation(
        db, uid, masjid.masjid_id, status=DonationStatus.COMPLETED
    )
    await seed.commit()

    r = await client.post(
        f"/payments/sslcommerz/redirect/fail?donation_id={completed.donation_id}",
        follow_redirects=False,
    )
    assert r.status_code == 303

    await db.refresh(completed)
    assert completed.status == DonationStatus.COMPLETED.value


async def test_redirect_fail_on_get_does_not_write(client, db, seed):
    # The DB write is gated to POST (the real gateway method); a bare GET must
    # not let anyone fail another donor's pending donation by URL alone.
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    pending = await _pending_donation(db, uid, masjid.masjid_id)
    await seed.commit()

    r = await client.get(
        f"/payments/sslcommerz/redirect/fail?donation_id={pending.donation_id}",
        follow_redirects=False,
    )
    assert r.status_code == 303

    await db.refresh(pending)
    assert pending.status == DonationStatus.PENDING.value


async def test_donation_status_requires_auth(client):
    r = await client.get(f"/donations/{uuid.uuid4()}")
    assert r.status_code in (401, 403)


async def test_donation_status_404_for_non_owner(client, seed):
    # Authenticated, but the donation doesn't exist → owner-only 404.
    uid = await seed.user()
    await seed.commit()
    r = await client.get(f"/donations/{uuid.uuid4()}", headers=auth_headers(uid))
    assert r.status_code == 404
