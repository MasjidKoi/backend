"""HTTP smoke tests for the donation surfaces (PRD 05).

The PRD scopes deep testing to the money path (the ledger/gateway/recurring
unit tests). These are light wiring checks for the security-sensitive IPN
endpoint and the gateway redirect — they touch no real gateway and no network.
"""

import uuid

import pytest

from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


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


async def test_donation_status_requires_auth(client):
    r = await client.get(f"/donations/{uuid.uuid4()}")
    assert r.status_code in (401, 403)


async def test_donation_status_404_for_non_owner(client, seed):
    # Authenticated, but the donation doesn't exist → owner-only 404.
    uid = await seed.user()
    await seed.commit()
    r = await client.get(f"/donations/{uuid.uuid4()}", headers=auth_headers(uid))
    assert r.status_code == 404
