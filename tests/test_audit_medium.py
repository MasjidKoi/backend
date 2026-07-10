"""Regression tests for the CODEBASE_AUDIT medium findings.

- #5  public masjid endpoints must not leak moderation state / suspension_reason
- #7  campaign analytics must derive donor_count / average_donation from donations
"""

import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.core.security import CurrentUser
from app.models.donation import Donation
from app.models.enums import AdminRole, AuthAssuranceLevel, DonationStatus, MasjidStatus
from app.services.masjid_campaign_service import MasjidCampaignService
from app.services.masjid_service import MasjidService

pytestmark = pytest.mark.asyncio


def _platform_admin() -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(),
        email="admin@masjidkoi.me",
        role=AdminRole.PLATFORM_ADMIN,
        aal=AuthAssuranceLevel.AAL1,
    )


# ── #5 — moderation-state leak ────────────────────────────────────────────────


async def test_suspended_masjid_hidden_from_anonymous(db, seed):
    masjid = await seed.masjid()
    masjid.status = MasjidStatus.SUSPENDED.value
    masjid.suspension_reason = "Fake prayer times reported"
    await seed.commit()
    svc = MasjidService(db)

    # Anonymous viewer: a suspended masjid is indistinguishable from missing.
    with pytest.raises(HTTPException) as exc:
        await svc.get_by_id(masjid.masjid_id, viewer=None)
    assert exc.value.status_code == 404

    # Platform admin: full access, including the internal suspension_reason.
    resp = await svc.get_by_id(masjid.masjid_id, viewer=_platform_admin())
    assert resp.status == MasjidStatus.SUSPENDED
    assert resp.suspension_reason == "Fake prayer times reported"


async def test_active_masjid_public_but_reason_hidden(db, seed):
    masjid = await seed.masjid()  # status defaults to active
    await seed.commit()
    svc = MasjidService(db)
    # Anonymous can still read an active masjid — but never the moderation note.
    resp = await svc.get_by_id(masjid.masjid_id, viewer=None)
    assert resp.status == MasjidStatus.ACTIVE
    assert resp.suspension_reason is None


async def test_public_list_only_active_but_admin_sees_all(db, seed):
    active = await seed.masjid(name="Active One")
    suspended = await seed.masjid(name="Suspended One")
    suspended.status = MasjidStatus.SUSPENDED.value
    await seed.commit()
    svc = MasjidService(db)

    # Anonymous listing forced to Active regardless of the requested status.
    public = await svc.list_for_admin(
        status_filter="suspended",
        admin_region=None,
        verified=None,
        q=None,
        page=1,
        page_size=200,
        allow_all_statuses=False,
    )
    ids = {i.masjid_id for i in public.items}
    assert active.masjid_id in ids
    assert suspended.masjid_id not in ids

    # Platform admin may filter to suspended.
    admin = await svc.list_for_admin(
        status_filter="suspended",
        admin_region=None,
        verified=None,
        q=None,
        page=1,
        page_size=200,
        allow_all_statuses=True,
    )
    admin_ids = {i.masjid_id for i in admin.items}
    assert suspended.masjid_id in admin_ids
    assert active.masjid_id not in admin_ids


# ── #7 — campaign analytics ───────────────────────────────────────────────────


async def test_campaign_analytics_counts_real_donors(db, seed):
    masjid = await seed.masjid(donations_enabled=True)
    campaign = await seed.campaign(masjid.masjid_id)
    donor_a, donor_b = uuid.uuid4(), uuid.uuid4()
    # donor_a gives twice, donor_b once — all COMPLETED (100, 300, 200 → avg 200).
    for donor, amount in [
        (donor_a, "100.00"),
        (donor_a, "300.00"),
        (donor_b, "200.00"),
    ]:
        db.add(
            Donation(
                user_id=donor,
                masjid_id=masjid.masjid_id,
                campaign_id=campaign.campaign_id,
                category="campaign",
                status=DonationStatus.COMPLETED.value,
                gross_amount=Decimal(amount),
                fee_amount=Decimal("0.00"),
                net_amount=Decimal(amount),
            )
        )
    # A PENDING gift must not count.
    db.add(
        Donation(
            user_id=uuid.uuid4(),
            masjid_id=masjid.masjid_id,
            campaign_id=campaign.campaign_id,
            category="campaign",
            status=DonationStatus.PENDING.value,
            gross_amount=Decimal("999.00"),
            fee_amount=Decimal("0.00"),
            net_amount=Decimal("999.00"),
        )
    )
    await db.flush()

    analytics = await MasjidCampaignService(db).get_analytics(
        masjid.masjid_id, campaign.campaign_id, _platform_admin()
    )
    assert analytics.donor_count == 2  # distinct completed donors
    assert analytics.average_donation == Decimal("200.00")  # 600 / 3 completed


async def test_campaign_analytics_zero_when_no_donations(db, seed):
    masjid = await seed.masjid(donations_enabled=True)
    campaign = await seed.campaign(masjid.masjid_id)
    analytics = await MasjidCampaignService(db).get_analytics(
        masjid.masjid_id, campaign.campaign_id, _platform_admin()
    )
    assert analytics.donor_count == 0
    assert analytics.average_donation is None
