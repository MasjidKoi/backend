"""PRD 09 #1 — the 30-day account-deletion purge anonymises a soft-deleted
account across every user-linked table, deletes the two unsafe-to-keep tables,
and is idempotent. Anonymise-everything: rows survive (aggregates intact), only
the identity is severed.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.device_token import DeviceToken
from app.models.donation import Donation
from app.models.masjid_review import MasjidReview
from app.models.recurring_schedule import RecurringSchedule
from app.models.user_badge import UserBadge
from app.models.user_journal_entry import UserJournalEntry
from app.models.user_profile import UserProfile
from app.repositories.account_purge_repository import AccountPurgeRepository
from app.services.account_purge_service import AccountPurgeService

pytestmark = pytest.mark.asyncio


async def _mark_deleted(db, uid: uuid.UUID, *, days_ago: int) -> UserProfile:
    profile = await db.get(UserProfile, uid)
    profile.is_deleted = True
    profile.deletion_requested_at = datetime.now(timezone.utc) - timedelta(
        days=days_ago
    )
    await db.commit()
    return profile


async def test_purge_anonymizes_user_data(client, seed, db):
    uid = await seed.user()
    masjid = await seed.masjid(donations_enabled=True)
    db.add(
        MasjidReview(masjid_id=masjid.masjid_id, user_id=uid, rating=5, body="Great")
    )
    db.add(UserJournalEntry(user_id=uid, entry_date=date.today(), fajr=True))
    db.add(UserBadge(user_id=uid, badge_type="GenerousGiver", tier=1))
    db.add(
        Donation(
            user_id=uid,
            masjid_id=masjid.masjid_id,
            category="general",
            status="completed",
            gross_amount=Decimal("100.00"),
            fee_amount=Decimal("0.00"),
            net_amount=Decimal("100.00"),
            donor_name="Real Name",
            donor_email="real@donor.test",
        )
    )
    await seed.device(uid)
    db.add(
        RecurringSchedule(
            user_id=uid,
            masjid_id=masjid.masjid_id,
            category="general",
            amount=Decimal("100.00"),
            frequency="monthly",
            start_date=date.today(),
            next_due_at=datetime.now(timezone.utc),
        )
    )
    await seed.commit()
    profile = await _mark_deleted(db, uid, days_ago=31)

    pseudonym = await AccountPurgeService(db).purge_profile(profile)
    seed.user_ids.append(pseudonym)  # so the remapped rows are cleaned up
    db.expire_all()

    async def count(model, col, value):
        return (
            await db.execute(select(func.count()).where(getattr(model, col) == value))
        ).scalar_one()

    # Content rows kept, but re-keyed off the real identity onto the pseudonym.
    for model, col in [
        (MasjidReview, "user_id"),
        (UserJournalEntry, "user_id"),
        (UserBadge, "user_id"),
        (Donation, "user_id"),
    ]:
        assert await count(model, col, uid) == 0
        assert await count(model, col, pseudonym) == 1

    # Donor PII blanked on the surviving donation row.
    name, email = (
        await db.execute(
            select(Donation.donor_name, Donation.donor_email).where(
                Donation.user_id == pseudonym
            )
        )
    ).one()
    assert name is None and email is None

    # Unsafe-to-keep tables hard-deleted (no token keeps pushing / schedule charging).
    assert await count(DeviceToken, "user_id", uid) == 0
    assert await count(DeviceToken, "user_id", pseudonym) == 0
    assert await count(RecurringSchedule, "user_id", uid) == 0
    assert await count(RecurringSchedule, "user_id", pseudonym) == 0

    # Profile reduced to a stripped tombstone under its ORIGINAL id (410 guard lives).
    name, photo, deleted, purged = (
        await db.execute(
            select(
                UserProfile.display_name,
                UserProfile.profile_photo_url,
                UserProfile.is_deleted,
                UserProfile.purged_at,
            ).where(UserProfile.user_id == uid)
        )
    ).one()
    assert name is None and photo is None
    assert deleted is True
    assert purged is not None


async def test_find_due_respects_window(seed, db):
    old = await seed.user()
    recent = await seed.user()
    active = await seed.user()  # never deleted
    await seed.commit()
    await _mark_deleted(db, old, days_ago=31)
    await _mark_deleted(db, recent, days_ago=1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    due_ids = {
        p.user_id for p in await AccountPurgeRepository(db).find_due(cutoff, 500)
    }

    assert old in due_ids
    assert recent not in due_ids
    assert active not in due_ids


async def test_run_due_is_idempotent(seed, db):
    uid = await seed.user()
    await seed.device(uid)  # deleted by the purge — leaves no orphan
    await seed.commit()
    await _mark_deleted(db, uid, days_ago=31)

    svc = AccountPurgeService(db)
    purged = await svc.run_due()
    assert purged >= 1

    # purged_at now set → the row no longer surfaces as due (a re-sweep is a no-op).
    db.expire_all()
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    due_ids = {
        p.user_id for p in await AccountPurgeRepository(db).find_due(cutoff, 500)
    }
    assert uid not in due_ids
