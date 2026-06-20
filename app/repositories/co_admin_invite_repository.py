import uuid

from sqlalchemy import func, select

from app.models.masjid_co_admin_invite import MasjidCoAdminInvite
from app.repositories.base import BaseRepository


class CoAdminInviteRepository(BaseRepository[MasjidCoAdminInvite]):
    model = MasjidCoAdminInvite

    async def get_pending_by_email_masjid(
        self, email: str, masjid_id: uuid.UUID
    ) -> MasjidCoAdminInvite | None:
        result = await self.db.execute(
            select(MasjidCoAdminInvite).where(
                MasjidCoAdminInvite.invited_email == email,
                MasjidCoAdminInvite.masjid_id == masjid_id,
                MasjidCoAdminInvite.status == "Pending",
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_gotrue_user_masjid(
        self, gotrue_user_id: uuid.UUID, masjid_id: uuid.UUID
    ) -> MasjidCoAdminInvite | None:
        result = await self.db.execute(
            select(MasjidCoAdminInvite).where(
                MasjidCoAdminInvite.gotrue_user_id == gotrue_user_id,
                MasjidCoAdminInvite.masjid_id == masjid_id,
                MasjidCoAdminInvite.status.in_(["Pending", "Accepted"]),
            )
        )
        return result.scalar_one_or_none()

    async def list_by_masjid(
        self, masjid_id: uuid.UUID, offset: int, limit: int
    ) -> tuple[list[MasjidCoAdminInvite], int]:
        count_q = select(func.count()).where(MasjidCoAdminInvite.masjid_id == masjid_id)
        rows_q = (
            select(MasjidCoAdminInvite)
            .where(MasjidCoAdminInvite.masjid_id == masjid_id)
            .order_by(MasjidCoAdminInvite.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        total = (await self.db.execute(count_q)).scalar_one()
        rows = list((await self.db.execute(rows_q)).scalars().all())
        return rows, total

    async def get_pending_by_id_masjid(
        self, invite_id: uuid.UUID, masjid_id: uuid.UUID
    ) -> MasjidCoAdminInvite | None:
        result = await self.db.execute(
            select(MasjidCoAdminInvite).where(
                MasjidCoAdminInvite.invite_id == invite_id,
                MasjidCoAdminInvite.masjid_id == masjid_id,
                MasjidCoAdminInvite.status == "Pending",
            )
        )
        return result.scalar_one_or_none()

    async def get_pending_by_gotrue_user(
        self, gotrue_user_id: uuid.UUID
    ) -> MasjidCoAdminInvite | None:
        result = await self.db.execute(
            select(MasjidCoAdminInvite).where(
                MasjidCoAdminInvite.gotrue_user_id == gotrue_user_id,
                MasjidCoAdminInvite.status == "Pending",
            )
        )
        return result.scalar_one_or_none()

    async def masjid_has_claimed_admin(self, masjid_id: uuid.UUID) -> bool:
        """True if the masjid has a claimed admin.

        An *Accepted* co-admin invite is the only local signal that a masjid_admin
        account is bound to this masjid (admins live in GoTrue, not a local table).
        Backs the moderation-routing predicate (Gap #10).
        """
        result = await self.db.execute(
            select(MasjidCoAdminInvite.invite_id)
            .where(MasjidCoAdminInvite.masjid_id == masjid_id)
            .where(MasjidCoAdminInvite.status == "Accepted")
            .limit(1)
        )
        return result.first() is not None
