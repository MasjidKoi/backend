import uuid

from sqlalchemy import func, select

from app.models.masjid_campaign import MasjidCampaign
from app.repositories.base import BaseRepository


class MasjidCampaignRepository(BaseRepository[MasjidCampaign]):
    model = MasjidCampaign

    async def get_by_id_and_masjid(
        self, campaign_id: uuid.UUID, masjid_id: uuid.UUID
    ) -> MasjidCampaign | None:
        result = await self.db.execute(
            select(MasjidCampaign).where(
                MasjidCampaign.campaign_id == campaign_id,
                MasjidCampaign.masjid_id == masjid_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_masjid(
        self,
        masjid_id: uuid.UUID,
        offset: int,
        limit: int,
        status_filter: str | None,
    ) -> tuple[list[MasjidCampaign], int]:
        base_where = [MasjidCampaign.masjid_id == masjid_id]
        if status_filter:
            base_where.append(MasjidCampaign.status == status_filter)

        count_result = await self.db.execute(select(func.count()).where(*base_where))
        total = count_result.scalar_one()

        rows_result = await self.db.execute(
            select(MasjidCampaign)
            .where(*base_where)
            .order_by(MasjidCampaign.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(rows_result.scalars().all()), total

    async def create(self, **fields) -> MasjidCampaign:
        campaign = MasjidCampaign(**fields)
        self.db.add(campaign)
        await self.db.flush()
        return campaign

    async def update(self, campaign: MasjidCampaign, fields: dict) -> MasjidCampaign:
        for k, v in fields.items():
            setattr(campaign, k, v)
        await self.db.flush()
        return campaign

    async def get_active_count(self) -> int:
        result = await self.db.execute(
            select(func.count()).where(MasjidCampaign.status == "Active")
        )
        return result.scalar_one()
