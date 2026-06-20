"""Journal endpoint contract tests (PRD 08, committed target).

Drives the public HTTP surface (in-process httpx, get_db override — the PRD 07
pattern). Pins: structured prayer-set validation, field-level update semantics
(a Qur'an edit leaves prayer logs untouched), backfill rejection for
streak-locked dates with notes/Qur'an still editable, and protected-day marker
round-trip.

``now`` can't be injected through the API, so the tests anchor dates to the real
Asia/Dhaka day: today is always open/editable; a date five days back is always
finalized (streak-locked).
"""

from datetime import datetime, timedelta, timezone

from app.services.streak_engine import DHAKA_TZ
from tests.conftest import auth_headers

TODAY = datetime.now(timezone.utc).astimezone(DHAKA_TZ).date()
LOCKED = TODAY - timedelta(days=5)

ALL_FIVE = {
    "fajr": True,
    "dhuhr": True,
    "asr": True,
    "maghrib": True,
    "isha": True,
}


async def test_structured_prayer_set_round_trip(client, seed):
    user = await seed.user()
    await seed.commit()

    resp = await client.post(
        "/users/me/journal",
        headers=auth_headers(user),
        json={"entry_date": TODAY.isoformat(), "prayers": ALL_FIVE},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prayers"] == ALL_FIVE
    assert body["is_protected"] is False
    assert body["quran"] is None


async def test_invalid_quran_unit_is_422(client, seed):
    user = await seed.user()
    await seed.commit()

    resp = await client.post(
        "/users/me/journal",
        headers=auth_headers(user),
        json={
            "entry_date": TODAY.isoformat(),
            "quran": {"amount": 5, "unit": "furlongs"},
        },
    )
    assert resp.status_code == 422


async def test_quran_edit_leaves_prayers_untouched(client, seed):
    user = await seed.user()
    await seed.commit()
    h = auth_headers(user)

    # Log all five prayers today.
    await client.post(
        "/users/me/journal",
        headers=h,
        json={"entry_date": TODAY.isoformat(), "prayers": ALL_FIVE},
    )
    # A Qur'an-only edit (no prayers key) must not clear the prayer logs.
    resp = await client.post(
        "/users/me/journal",
        headers=h,
        json={
            "entry_date": TODAY.isoformat(),
            "quran": {"amount": 10, "unit": "pages"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prayers"] == ALL_FIVE  # untouched
    assert body["quran"] == {"amount": 10, "unit": "pages"}


async def test_protected_marker_round_trip(client, seed):
    user = await seed.user()
    await seed.commit()

    resp = await client.post(
        "/users/me/journal",
        headers=auth_headers(user),
        json={"entry_date": TODAY.isoformat(), "is_protected": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_protected"] is True


async def test_backfill_rejects_prayer_edit_on_locked_date(client, seed):
    user = await seed.user()
    await seed.commit()

    resp = await client.post(
        "/users/me/journal",
        headers=auth_headers(user),
        json={"entry_date": LOCKED.isoformat(), "prayers": ALL_FIVE},
    )
    assert resp.status_code == 409, resp.text


async def test_backfill_allows_notes_and_quran_on_locked_date(client, seed):
    user = await seed.user()
    await seed.commit()

    resp = await client.post(
        "/users/me/journal",
        headers=auth_headers(user),
        json={
            "entry_date": LOCKED.isoformat(),
            "notes": "late reflection",
            "quran": {"amount": 3, "unit": "juz"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["notes"] == "late reflection"
    assert body["quran"] == {"amount": 3, "unit": "juz"}
