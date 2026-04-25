import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.repositories.masjid_campaign_repository import MasjidCampaignRepository
from app.repositories.masjid_repository import MasjidRepository
from app.schemas.masjid_campaign import (
    CampaignAnalyticsResponse,
    CampaignCreate,
    CampaignListResponse,
    CampaignResponse,
    CampaignUpdate,
)


class MasjidCampaignService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = MasjidCampaignRepository(db)
        self.masjid_repo = MasjidRepository(db)

    def _check_scope(self, user: CurrentUser, masjid_id: uuid.UUID) -> None:
        if user.is_platform_admin:
            return
        if user.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to your own masjid",
            )

    def _to_response(self, campaign) -> CampaignResponse:
        today = date.today()
        target = campaign.target_amount or Decimal("0")
        raised = campaign.raised_amount or Decimal("0")
        progress_pct = round(float(raised / target * 100), 2) if target > 0 else 0.0
        days_remaining = max(0, (campaign.end_date - today).days)
        return CampaignResponse(
            campaign_id=campaign.campaign_id,
            masjid_id=campaign.masjid_id,
            title=campaign.title,
            description=campaign.description,
            target_amount=campaign.target_amount,
            raised_amount=campaign.raised_amount,
            progress_pct=progress_pct,
            banner_url=campaign.banner_url,
            start_date=campaign.start_date,
            end_date=campaign.end_date,
            days_remaining=days_remaining,
            status=campaign.status,
            created_by_email=campaign.created_by_email,
            created_at=campaign.created_at,
            updated_at=campaign.updated_at,
        )

    async def list_campaigns(
        self,
        masjid_id: uuid.UUID,
        page: int,
        page_size: int,
        status_filter: str | None,
    ) -> CampaignListResponse:
        offset = (page - 1) * page_size
        rows, total = await self.repo.list_by_masjid(
            masjid_id, offset, page_size, status_filter
        )
        return CampaignListResponse(
            items=[self._to_response(c) for c in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def create_campaign(
        self,
        masjid_id: uuid.UUID,
        user: CurrentUser,
        data: CampaignCreate,
    ) -> CampaignResponse:
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Masjid not found"
            )
        self._check_scope(user, masjid_id)

        campaign = await self.repo.create(
            masjid_id=masjid_id,
            title=data.title,
            description=data.description,
            target_amount=data.target_amount,
            raised_amount=Decimal("0.00"),
            banner_url=data.banner_url,
            start_date=data.start_date,
            end_date=data.end_date,
            status="Active",
            created_by_id=uuid.UUID(str(user.user_id)),
            created_by_email=user.email,
        )
        await self.repo.commit()
        return self._to_response(campaign)

    async def update_campaign(
        self,
        masjid_id: uuid.UUID,
        campaign_id: uuid.UUID,
        user: CurrentUser,
        data: CampaignUpdate,
    ) -> CampaignResponse:
        campaign = await self.repo.get_by_id_and_masjid(campaign_id, masjid_id)
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found"
            )
        self._check_scope(user, masjid_id)

        fields = data.model_dump(exclude_unset=True)

        # validate date range after applying proposed changes
        new_start = fields.get("start_date", campaign.start_date)
        new_end = fields.get("end_date", campaign.end_date)
        if new_end < new_start:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="end_date must be on or after start_date",
            )

        await self.repo.update(campaign, fields)
        await self.repo.commit()
        # reload — onupdate=func.now() expires updated_at after flush
        campaign = await self.repo.get_by_id_and_masjid(campaign_id, masjid_id)
        return self._to_response(campaign)

    async def get_analytics(
        self,
        masjid_id: uuid.UUID,
        campaign_id: uuid.UUID,
        user: CurrentUser,
    ) -> CampaignAnalyticsResponse:
        self._check_scope(user, masjid_id)
        campaign = await self.repo.get_by_id_and_masjid(campaign_id, masjid_id)
        if not campaign:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found"
            )

        today = date.today()
        target = campaign.target_amount or Decimal("0")
        raised = campaign.raised_amount or Decimal("0")
        progress_pct = round(float(raised / target * 100), 2) if target > 0 else 0.0
        days_remaining = max(0, (campaign.end_date - today).days)

        return CampaignAnalyticsResponse(
            campaign_id=campaign.campaign_id,
            title=campaign.title,
            status=campaign.status,
            target_amount=campaign.target_amount,
            raised_amount=campaign.raised_amount,
            progress_pct=progress_pct,
            days_remaining=days_remaining,
            donor_count=0,
            average_donation=None,
        )
