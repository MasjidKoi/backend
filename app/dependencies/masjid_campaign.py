from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.masjid_campaign_service import MasjidCampaignService


def get_masjid_campaign_service(
    db: AsyncSession = Depends(get_db),
) -> MasjidCampaignService:
    return MasjidCampaignService(db)
