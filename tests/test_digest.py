"""DigestScheduler tests (PRD 07, committed target).

Drive the public module output — DigestService.run_for_hour(hour, now) returns
the list of digests it sent. Pins: hour-bucket selection against Asia/Dhaka,
digest/instant/mute routing, empty-digest suppression, one-push-per-user-per-day
idempotency, and stability across a re-run within the same hour.
"""

from datetime import datetime, timedelta, timezone

from app.services.digest_service import DHAKA_TZ, DigestService

# A fixed "now" whose Dhaka wall-clock hour is 19:00 (UTC 13:00 + 6h).
NOW = datetime(2026, 6, 16, 13, 0, tzinfo=timezone.utc)
DHAKA_HOUR = NOW.astimezone(DHAKA_TZ).hour  # == 19


async def _published_now(seed, masjid_id):
    # Within the 24h digest window, after any prior last_digest_sent_at.
    await seed.announcement(masjid_id, published_at=NOW - timedelta(hours=1))


async def test_digest_sent_for_due_user_with_digest_follow(db, seed):
    user = await seed.user(digest_hour=DHAKA_HOUR)
    m = await seed.masjid()
    await seed.follow(user, m.masjid_id, mode="digest")
    await _published_now(seed, m.masjid_id)
    await seed.commit()

    sends = await DigestService(db).run_for_hour(DHAKA_HOUR, NOW)
    mine = [s for s in sends if s.user_id == str(user)]
    assert len(mine) == 1
    assert mine[0].announcement_count == 1
    assert mine[0].masjid_count == 1


async def test_hour_bucket_excludes_other_hours(db, seed):
    user = await seed.user(digest_hour=(DHAKA_HOUR + 1) % 24)
    m = await seed.masjid()
    await seed.follow(user, m.masjid_id, mode="digest")
    await _published_now(seed, m.masjid_id)
    await seed.commit()

    sends = await DigestService(db).run_for_hour(DHAKA_HOUR, NOW)
    assert all(s.user_id != str(user) for s in sends)


async def test_instant_and_mute_follows_not_digested(db, seed):
    user = await seed.user(digest_hour=DHAKA_HOUR)
    instant_m = await seed.masjid("instant")
    mute_m = await seed.masjid("mute")
    await seed.follow(user, instant_m.masjid_id, mode="instant")
    await seed.follow(user, mute_m.masjid_id, mode="mute")
    await _published_now(seed, instant_m.masjid_id)
    await _published_now(seed, mute_m.masjid_id)
    await seed.commit()

    sends = await DigestService(db).run_for_hour(DHAKA_HOUR, NOW)
    # No digest-mode follow with new announcements → nothing for this user.
    assert all(s.user_id != str(user) for s in sends)


async def test_empty_digest_suppressed(db, seed):
    user = await seed.user(digest_hour=DHAKA_HOUR)
    m = await seed.masjid()
    await seed.follow(user, m.masjid_id, mode="digest")
    # No announcements published → nothing to digest.
    await seed.commit()

    sends = await DigestService(db).run_for_hour(DHAKA_HOUR, NOW)
    assert all(s.user_id != str(user) for s in sends)


async def test_one_push_per_day_idempotent_on_rerun(db, seed):
    user = await seed.user(digest_hour=DHAKA_HOUR)
    m = await seed.masjid()
    await seed.follow(user, m.masjid_id, mode="digest")
    await _published_now(seed, m.masjid_id)
    await seed.commit()

    svc = DigestService(db)
    first = await svc.run_for_hour(DHAKA_HOUR, NOW)
    assert any(s.user_id == str(user) for s in first)

    # Re-run within the same Dhaka day (e.g. scheduler restart) → no re-send.
    second = await svc.run_for_hour(DHAKA_HOUR, NOW + timedelta(minutes=5))
    assert all(s.user_id != str(user) for s in second)


async def test_announcements_aggregate_across_digest_masjids(db, seed):
    user = await seed.user(digest_hour=DHAKA_HOUR)
    m1 = await seed.masjid("one")
    m2 = await seed.masjid("two")
    await seed.follow(user, m1.masjid_id, mode="digest")
    await seed.follow(user, m2.masjid_id, mode="digest")
    await _published_now(seed, m1.masjid_id)
    await _published_now(seed, m1.masjid_id)
    await _published_now(seed, m2.masjid_id)
    await seed.commit()

    sends = await DigestService(db).run_for_hour(DHAKA_HOUR, NOW)
    mine = [s for s in sends if s.user_id == str(user)]
    assert len(mine) == 1
    assert mine[0].announcement_count == 3
    assert mine[0].masjid_count == 2
