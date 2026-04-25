import uuid

from fastapi import APIRouter, Depends, Query, status

from app.core.security import CurrentUser
from app.dependencies.auth import require_masjid_admin
from app.dependencies.masjid_campaign import get_masjid_campaign_service
from app.schemas.masjid_campaign import (
    CampaignAnalyticsResponse,
    CampaignCreate,
    CampaignListResponse,
    CampaignResponse,
    CampaignStatus,
    CampaignUpdate,
)
from app.services.masjid_campaign_service import MasjidCampaignService

router = APIRouter(prefix="/masjids", tags=["campaigns"])


@router.get(
    "/{masjid_id}/campaigns",
    response_model=CampaignListResponse,
    summary="List fundraising campaigns for a masjid — public, paginated",
)
async def list_campaigns(
    masjid_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: CampaignStatus | None = Query(default=None),
    service: MasjidCampaignService = Depends(get_masjid_campaign_service),
) -> CampaignListResponse:
    return await service.list_campaigns(masjid_id, page, page_size, status)


@router.post(
    "/{masjid_id}/campaigns",
    response_model=CampaignResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a fundraising campaign (masjid_admin)",
)
async def create_campaign(
    masjid_id: uuid.UUID,
    body: CampaignCreate,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidCampaignService = Depends(get_masjid_campaign_service),
) -> CampaignResponse:
    return await service.create_campaign(masjid_id, user, body)


@router.patch(
    "/{masjid_id}/campaigns/{campaign_id}",
    response_model=CampaignResponse,
    summary="Update a fundraising campaign (masjid_admin)",
)
async def update_campaign(
    masjid_id: uuid.UUID,
    campaign_id: uuid.UUID,
    body: CampaignUpdate,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidCampaignService = Depends(get_masjid_campaign_service),
) -> CampaignResponse:
    return await service.update_campaign(masjid_id, campaign_id, user, body)


@router.get(
    "/{masjid_id}/campaigns/{campaign_id}/analytics",
    response_model=CampaignAnalyticsResponse,
    summary="Campaign performance analytics (masjid_admin)",
)
async def get_campaign_analytics(
    masjid_id: uuid.UUID,
    campaign_id: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidCampaignService = Depends(get_masjid_campaign_service),
) -> CampaignAnalyticsResponse:
    return await service.get_analytics(masjid_id, campaign_id, user)
