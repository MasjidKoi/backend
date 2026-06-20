"""PRD 09 — the full user data export must carry every user-linked collection,
not just profile + follows.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.donation import Donation
from app.models.masjid_review import MasjidReview
from app.models.user_journal_entry import UserJournalEntry
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def test_export_includes_user_collections(client, seed, db):
    uid = await seed.user()
    masjid = await seed.masjid()
    await seed.follow(uid, masjid.masjid_id)
    db.add(
        MasjidReview(masjid_id=masjid.masjid_id, user_id=uid, rating=5, body="Great")
    )
    db.add(UserJournalEntry(user_id=uid, entry_date=date.today(), fajr=True))
    db.add(
        Donation(
            user_id=uid,
            masjid_id=masjid.masjid_id,
            category="general",
            status="completed",
            gross_amount=Decimal("100.00"),
            fee_amount=Decimal("0.00"),
            net_amount=Decimal("100.00"),
        )
    )
    await seed.commit()

    r = await client.get("/users/me/export", headers=auth_headers(uid))
    assert r.status_code == 200
    data = r.json()

    # Existing surface still present…
    assert any(
        f["masjid_id"] == str(masjid.masjid_id) for f in data["followed_masjids"]
    )
    # …plus the newly-exported collections.
    assert any(rv["body"] == "Great" and rv["rating"] == 5 for rv in data["reviews"])
    assert len(data["journal_entries"]) == 1
    assert data["journal_entries"][0]["fajr"] is True
    assert any(d["category"] == "general" for d in data["donations"])
    # Every category key exists even when empty (complete, predictable shape).
    for key in ("questions", "submissions", "checkins", "goals", "badges",
                "support_tickets", "device_tokens", "reports",
                "recurring_schedules", "event_rsvps"):
        assert key in data
