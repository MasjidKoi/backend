"""Community Pillar contribution-counter tests (PRD 08).

Pins the counter feeding the Community Pillar badge: approved COMMUNITY photos
count toward it alongside check-ins (1 point each), while pending/rejected
photos, admin-gallery photos, and other users' photos never count. Drives the
public ``GamificationService.reevaluate_badges`` over seeded rows; the pure
tier/threshold logic itself lives in ``test_badge_engine.py``.
"""

import uuid

from app.models.enums import BadgeType, PhotoModerationStatus, PhotoSource
from app.models.masjid import MasjidPhoto
from app.models.masjid_report import MasjidReport
from app.models.user_checkin import UserCheckin
from app.services.gamification_service import GamificationService

PILLAR = BadgeType.COMMUNITY_PILLAR.value  # "CommunityPillar"


async def _add_checkins(db, user_id, masjid_id, n):
    for _ in range(n):
        db.add(UserCheckin(user_id=user_id, masjid_id=masjid_id))
    await db.flush()


async def _add_report(db, masjid_id, *, user_id, status):
    db.add(
        MasjidReport(
            masjid_id=masjid_id,
            user_id=user_id,
            field_name="address",
            description="The address shown is out of date.",
            status=status,
        )
    )
    await db.flush()


async def _add_photo(db, masjid_id, *, uploaded_by, source, status):
    db.add(
        MasjidPhoto(
            masjid_id=masjid_id,
            url="https://example.test/photo.jpg",
            source=source,
            status=status,
            uploaded_by=uploaded_by,
        )
    )
    await db.flush()


async def test_approved_community_photos_award_community_pillar(db, seed):
    """10 approved community photos (no check-ins) reach tier 1 on photos alone."""
    user = await seed.user()
    masjid = await seed.masjid()
    for _ in range(10):
        await _add_photo(
            db,
            masjid.masjid_id,
            uploaded_by=user,
            source=PhotoSource.COMMUNITY,
            status=PhotoModerationStatus.APPROVED,
        )
    await seed.commit()

    awarded = await GamificationService(db).reevaluate_badges(user)

    tiers = {b.tier for b in awarded if b.badge_type == PILLAR}
    assert 1 in tiers  # threshold 10 reached
    assert 2 not in tiers  # threshold 50 not reached


async def test_checkins_and_photos_sum_into_contribution_points(db, seed):
    """Check-ins and approved photos add together — 5 + 5 = 10 → tier 1."""
    user = await seed.user()
    masjid = await seed.masjid()
    await _add_checkins(db, user, masjid.masjid_id, 5)
    for _ in range(5):
        await _add_photo(
            db,
            masjid.masjid_id,
            uploaded_by=user,
            source=PhotoSource.COMMUNITY,
            status=PhotoModerationStatus.APPROVED,
        )
    await seed.commit()

    awarded = await GamificationService(db).reevaluate_badges(user)

    assert any(b.badge_type == PILLAR and b.tier == 1 for b in awarded)


async def test_noncounting_photos_are_excluded(db, seed):
    """Pending/rejected/admin/other-user photos don't count: 9 genuine
    contributions stay below the threshold-10 tier despite the noise."""
    user = await seed.user()
    other = uuid.uuid4()
    masjid = await seed.masjid()

    # 9 genuine contributions — one short of tier 1.
    await _add_checkins(db, user, masjid.masjid_id, 4)
    for _ in range(5):
        await _add_photo(
            db,
            masjid.masjid_id,
            uploaded_by=user,
            source=PhotoSource.COMMUNITY,
            status=PhotoModerationStatus.APPROVED,
        )

    # Noise that must NOT count toward this user's Community Pillar.
    await _add_photo(
        db,
        masjid.masjid_id,
        uploaded_by=user,
        source=PhotoSource.COMMUNITY,
        status=PhotoModerationStatus.PENDING,
    )
    await _add_photo(
        db,
        masjid.masjid_id,
        uploaded_by=user,
        source=PhotoSource.COMMUNITY,
        status=PhotoModerationStatus.REJECTED,
    )
    await _add_photo(
        db,
        masjid.masjid_id,
        uploaded_by=user,
        source=PhotoSource.ADMIN,
        status=PhotoModerationStatus.APPROVED,
    )
    await _add_photo(
        db,
        masjid.masjid_id,
        uploaded_by=other,
        source=PhotoSource.COMMUNITY,
        status=PhotoModerationStatus.APPROVED,
    )
    await seed.commit()

    awarded = await GamificationService(db).reevaluate_badges(user)

    assert not any(b.badge_type == PILLAR for b in awarded)  # 9 < 10


async def test_accepted_reports_count_toward_pillar(db, seed):
    """Resolved (accepted) reports attributed to the user add contribution
    points; pending/unattributed ones don't. 5 check-ins + 5 resolved reports
    = 10 → tier 1, while pending and guest reports are noise."""
    user = await seed.user()
    other = uuid.uuid4()
    masjid = await seed.masjid()

    await _add_checkins(db, user, masjid.masjid_id, 5)
    for _ in range(5):
        await _add_report(db, masjid.masjid_id, user_id=user, status="resolved")

    # Noise that must NOT count: not-yet-accepted, and another user's report.
    await _add_report(db, masjid.masjid_id, user_id=user, status="pending")
    await _add_report(db, masjid.masjid_id, user_id=user, status="reviewed")
    await _add_report(db, masjid.masjid_id, user_id=other, status="resolved")
    await _add_report(db, masjid.masjid_id, user_id=None, status="resolved")  # guest
    await seed.commit()

    awarded = await GamificationService(db).reevaluate_badges(user)

    assert any(b.badge_type == PILLAR and b.tier == 1 for b in awarded)


async def test_unaccepted_reports_alone_do_not_award(db, seed):
    """9 genuine contributions + only pending reports stays below tier 1."""
    user = await seed.user()
    masjid = await seed.masjid()

    await _add_checkins(db, user, masjid.masjid_id, 9)
    await _add_report(db, masjid.masjid_id, user_id=user, status="pending")
    await _add_report(db, masjid.masjid_id, user_id=user, status="reviewed")
    await seed.commit()

    awarded = await GamificationService(db).reevaluate_badges(user)

    assert not any(b.badge_type == PILLAR for b in awarded)  # 9 < 10
