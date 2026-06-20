"""PRD 05 #3 — "donate anonymously by default".

The preference persists through the notification-preferences endpoint and seeds
DonationCreate.is_anonymous when the client omits it; an explicit value always
wins. The donation create path is exercised at the service seam with a fake
gateway (no network), mirroring the ledger tests.
"""

import uuid
from decimal import Decimal

import pytest

from app.core.security import CurrentUser
from app.models.enums import AdminRole, AuthAssuranceLevel
from app.models.user_profile import UserProfile
from app.services.donation_service import DonationService
from app.services.sslcommerz_gateway import SessionResult
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


class _FakeGateway:
    """Only create_session is hit by create_pending's happy path."""

    async def create_session(self, *, tran_id, amount, **kw) -> SessionResult:
        return SessionResult(gateway_url=f"https://gw/pay/{tran_id}", session_key="sk")


def _user(uid: uuid.UUID) -> CurrentUser:
    return CurrentUser(
        user_id=uid,
        email="donor@test.local",
        role=AdminRole.APP_USER,
        aal=AuthAssuranceLevel.AAL1,
    )


async def _set_default(db, uid: uuid.UUID, value: bool) -> None:
    profile = await db.get(UserProfile, uid)
    profile.donate_anonymously_by_default = value
    await db.commit()


async def test_setting_persists_via_notification_preferences(client, seed, db):
    uid = await seed.user()
    await seed.commit()

    r = await client.patch(
        "/users/me/notification-preferences",
        json={"donate_anonymously_by_default": True},
        headers=auth_headers(uid),
    )
    assert r.status_code == 200
    assert r.json()["donate_anonymously_by_default"] is True

    # And it surfaces on the profile read.
    r = await client.get("/users/me", headers=auth_headers(uid))
    assert r.json()["donate_anonymously_by_default"] is True


async def test_explicit_null_preference_is_a_noop_not_a_500(client, seed, db):
    # An explicit JSON null on a NOT NULL preference column must be ignored, not
    # attempted as a NULL UPDATE (which would 500 on the constraint).
    uid = await seed.user()
    await seed.commit()

    r = await client.patch(
        "/users/me/notification-preferences",
        json={"digest_hour": None, "donate_anonymously_by_default": True},
        headers=auth_headers(uid),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["donate_anonymously_by_default"] is True
    assert isinstance(body["digest_hour"], int)  # untouched, still the default


async def test_default_seeds_is_anonymous_when_omitted(seed, db):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    await seed.commit()
    await _set_default(db, uid, True)

    svc = DonationService(db, gateway=_FakeGateway())
    result = await svc.create_pending(
        _user(uid),
        masjid_id=masjid.masjid_id,
        amount=Decimal("100.00"),
        category="general",
        is_anonymous=None,  # client omitted it
    )
    assert result.donation.is_anonymous is True


async def test_explicit_value_overrides_default(seed, db):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    await seed.commit()
    await _set_default(db, uid, True)  # default ON…

    svc = DonationService(db, gateway=_FakeGateway())
    result = await svc.create_pending(
        _user(uid),
        masjid_id=masjid.masjid_id,
        amount=Decimal("100.00"),
        category="general",
        is_anonymous=False,  # …but the client explicitly opts in to being named
    )
    assert result.donation.is_anonymous is False
