"""
Public app-config router (PRD 03).

Exposes the read-only subset of platform settings that mobile clients need at
startup — most importantly the Hijri-date offset. Unauthenticated by design:
this is reference data every client reads, unlike the admin-only
``GET /admin/settings`` which returns the full (sensitive) settings row.
"""

from fastapi import APIRouter, Depends

from app.dependencies.platform_settings import get_platform_settings_service
from app.schemas.platform_settings import AppConfigResponse
from app.services.platform_settings_service import PlatformSettingsService

router = APIRouter(prefix="/app-config", tags=["config"])


@router.get(
    "",
    response_model=AppConfigResponse,
    summary="Public app configuration (Hijri offset, calc defaults, branding)",
)
async def get_app_config(
    service: PlatformSettingsService = Depends(get_platform_settings_service),
) -> AppConfigResponse:
    settings = await service.get()
    return AppConfigResponse.model_validate(settings)
