import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.donation import Donation
from app.models.donation_receipt_counter import DonationReceiptCounter
from app.models.enums import DonationStatus
from app.models.masjid import Masjid
from app.models.masjid_campaign import MasjidCampaign
from app.repositories.base import BaseRepository

# Postgres tz name for the Dhaka day/month boundary. Asia/Dhaka is a fixed
# UTC+6 with no DST, matching app.core.time.DHAKA_TZ — the two never drift.
_DHAKA = "Asia/Dhaka"


class DonationRepository(BaseRepository[Donation]):
    model = Donation

    # ── Atomic completion-transaction helpers ─────────────────────────────────

    async def get_for_update(self, donation_id: uuid.UUID) -> Donation | None:
        """Fetch a donation row with ``SELECT … FOR UPDATE`` so concurrent IPNs
        (or concurrent refunds) for the same donation serialise: the second
        caller blocks until the first commits, then sees the new status and
        no-ops instead of double-bumping the campaign or double-refunding."""
        result = await self.db.execute(
            select(Donation)
            .where(Donation.donation_id == donation_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_all_for_user(self, user_id: uuid.UUID) -> list[Donation]:
        """Every donation by this user, newest first — unpaginated, for the full
        data export (PRD 09)."""
        result = await self.db.execute(
            select(Donation)
            .where(Donation.user_id == user_id)
            .order_by(Donation.created_at.desc())
        )
        return list(result.scalars().all())

    async def bump_campaign_raised(
        self, campaign_id: uuid.UUID, delta: Decimal
    ) -> None:
        """Atomic ``raised_amount += delta`` — no read-modify-write, no recompute.

        Used with +gross on completion and −gross on refund, inside the same
        transaction as the donation's status flip.
        """
        await self.db.execute(
            update(MasjidCampaign)
            .where(MasjidCampaign.campaign_id == campaign_id)
            .values(raised_amount=MasjidCampaign.raised_amount + delta)
        )

    async def next_receipt_seq(self, year: int) -> int:
        """Allocate the next gapless receipt number for the year (atomic upsert)."""
        stmt = (
            pg_insert(DonationReceiptCounter)
            .values(year=year, last_number=1)
            .on_conflict_do_update(
                index_elements=[DonationReceiptCounter.year],
                set_={"last_number": DonationReceiptCounter.last_number + 1},
            )
            .returning(DonationReceiptCounter.last_number)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one()

    # ── Badge counter source ────────────────────────────────────────────────────

    async def completed_giving_months(self, user_id: uuid.UUID) -> set[tuple[int, int]]:
        """Distinct (year, month) in Asia/Dhaka with ≥1 completed donation.

        Feeds the Generous Giver badge counter (consistency, never amount).
        """
        month_expr = func.date_trunc(
            "month", func.timezone(_DHAKA, Donation.completed_at)
        )
        result = await self.db.execute(
            select(month_expr)
            .where(
                Donation.user_id == user_id,
                Donation.status == DonationStatus.COMPLETED,
                Donation.completed_at.is_not(None),
            )
            .group_by(month_expr)
        )
        return {(d.year, d.month) for d in result.scalars().all() if d is not None}

    # ── Balance ──────────────────────────────────────────────────────────────

    async def sum_net_completed(self, masjid_id: uuid.UUID) -> Decimal:
        """Total net of completed donations for a masjid (the credit side of the
        derived balance; disbursements are the debit side)."""
        result = await self.db.execute(
            select(func.coalesce(func.sum(Donation.net_amount), 0)).where(
                Donation.masjid_id == masjid_id,
                Donation.status == DonationStatus.COMPLETED,
            )
        )
        return Decimal(result.scalar_one())

    async def list_stale_pending(
        self, cutoff: datetime, limit: int = 500
    ) -> list[Donation]:
        """Lock PENDING donations older than the cutoff for the stale-pending
        sweep. ``FOR UPDATE SKIP LOCKED`` means a donation an IPN is mid-completing
        (it holds its own row lock) is skipped, never clobbered — and a donation
        completed-and-committed just before is excluded by the status predicate.
        The caller MUST flip these to FAILED and commit in the SAME transaction,
        so the lock window covers the read-modify-write. Once FAILED they're never
        selected again, so the recovery push never repeats."""
        rows = await self.db.execute(
            select(Donation)
            .where(
                Donation.status == DonationStatus.PENDING,
                Donation.created_at < cutoff,
            )
            .order_by(Donation.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(rows.scalars().all())

    async def campaign_donor_ids(self, campaign_id: uuid.UUID) -> list[uuid.UUID]:
        """Distinct users who completed a donation to a campaign — recipients of
        the campaign-funded milestone push."""
        rows = await self.db.execute(
            select(Donation.user_id)
            .where(
                Donation.campaign_id == campaign_id,
                Donation.status == DonationStatus.COMPLETED,
            )
            .group_by(Donation.user_id)
        )
        return [uid for (uid,) in rows.all()]

    async def net_by_masjid(self) -> dict[uuid.UUID, Decimal]:
        """Net completed donations grouped by masjid — credit side for the
        platform-wide balances view."""
        rows = await self.db.execute(
            select(Donation.masjid_id, func.coalesce(func.sum(Donation.net_amount), 0))
            .where(Donation.status == DonationStatus.COMPLETED)
            .group_by(Donation.masjid_id)
        )
        return {mid: Decimal(total) for mid, total in rows.all()}

    # ── Dashboard reads (donor-facing; gross everywhere) ─────────────────────────

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        masjid_id: uuid.UUID | None = None,
        category: str | None = None,
        status_: str | None = None,
        year: int | None = None,
        limit: int = 20,
        before: datetime | None = None,
    ) -> list[tuple[Donation, str]]:
        """A page of the donor's history, newest first, with masjid name.

        Keyset paginated on created_at (``before`` = the previous page's last
        created_at). Returns ``limit`` rows at most; the caller derives the next
        cursor from the last row.
        """
        conds = [Donation.user_id == user_id]
        if masjid_id is not None:
            conds.append(Donation.masjid_id == masjid_id)
        if category is not None:
            conds.append(Donation.category == category)
        if status_ is not None:
            conds.append(Donation.status == status_)
        if year is not None:
            conds.append(
                func.extract("year", func.timezone(_DHAKA, Donation.created_at)) == year
            )
        if before is not None:
            conds.append(Donation.created_at < before)
        rows = await self.db.execute(
            select(Donation, Masjid.name)
            .join(Masjid, Masjid.masjid_id == Donation.masjid_id)
            .where(*conds)
            .order_by(Donation.created_at.desc())
            .limit(limit)
        )
        return [(d, name) for d, name in rows.all()]

    async def summary_for_user(
        self, user_id: uuid.UUID, *, this_year: int
    ) -> tuple[Decimal, Decimal, list[tuple[uuid.UUID, str, Decimal]]]:
        """Lifetime total, this-year total, and per-masjid totals — all GROSS of
        completed donations (donor-facing numbers are gross)."""
        completed = [
            Donation.user_id == user_id,
            Donation.status == DonationStatus.COMPLETED,
        ]
        gross_sum = func.coalesce(func.sum(Donation.gross_amount), 0)

        lifetime = Decimal(
            (await self.db.execute(select(gross_sum).where(*completed))).scalar_one()
        )
        year_total = Decimal(
            (
                await self.db.execute(
                    select(gross_sum).where(
                        *completed,
                        func.extract(
                            "year", func.timezone(_DHAKA, Donation.completed_at)
                        )
                        == this_year,
                    )
                )
            ).scalar_one()
        )
        per_masjid_rows = await self.db.execute(
            select(Donation.masjid_id, Masjid.name, gross_sum)
            .join(Masjid, Masjid.masjid_id == Donation.masjid_id)
            .where(*completed)
            .group_by(Donation.masjid_id, Masjid.name)
            .order_by(gross_sum.desc())
        )
        per_masjid = [
            (mid, name, Decimal(total)) for mid, name, total in per_masjid_rows.all()
        ]
        return lifetime, year_total, per_masjid

    async def list_completed_for_user_year(
        self, user_id: uuid.UUID, year: int
    ) -> list[Donation]:
        """All completed donations for a donor in a Dhaka-calendar year — feeds
        the annual giving-summary PDF."""
        rows = await self.db.execute(
            select(Donation)
            .where(
                Donation.user_id == user_id,
                Donation.status == DonationStatus.COMPLETED,
                func.extract("year", func.timezone(_DHAKA, Donation.completed_at))
                == year,
            )
            .order_by(Donation.completed_at.asc())
        )
        return list(rows.scalars().all())

    # ── Admin (masjid-scoped) ────────────────────────────────────────────────

    async def list_for_masjid(
        self,
        masjid_id: uuid.UUID,
        *,
        status_: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Donation], int]:
        conds = [Donation.masjid_id == masjid_id]
        if status_ is not None:
            conds.append(Donation.status == status_)
        total = (await self.db.execute(select(func.count()).where(*conds))).scalar_one()
        rows = (
            (
                await self.db.execute(
                    select(Donation)
                    .where(*conds)
                    .order_by(Donation.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return list(rows), total
