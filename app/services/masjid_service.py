import logging
import uuid

from fastapi import HTTPException, status
from geoalchemy2.shape import to_shape
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.models.enums import MasjidStatus
from app.models.masjid import Masjid
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.masjid_repository import MasjidRepository
from app.schemas.masjid import (
    ContactResponse,
    FacilitiesResponse,
    MasjidAdminListResponse,
    MasjidCreate,
    MasjidNearbyResult,
    MasjidResponse,
    MasjidSummary,
    MasjidUpdate,
    PhotoResponse,
)

logger = logging.getLogger(__name__)


class MasjidService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = MasjidRepository(db)
        self.audit = AuditLogRepository(db)  # Same session — commits atomically

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _get_or_404(self, masjid_id: uuid.UUID) -> Masjid:
        masjid = await self.repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Masjid not found",
            )
        return masjid

    def _check_scope(self, user: CurrentUser, masjid_id: uuid.UUID) -> None:
        """Platform admins bypass; masjid admins may only act on their own masjid."""
        if user.is_platform_admin:
            return
        if user.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to your own masjid",
            )

    def _to_response(self, masjid: Masjid) -> MasjidResponse:
        """
        Build MasjidResponse from ORM instance.
        Converts GeoAlchemy2 WKBElement → (latitude, longitude) floats here
        so the schema stays clean (no model_validator complexity).
        """
        point = to_shape(masjid.location)
        return MasjidResponse(
            masjid_id=masjid.masjid_id,
            name=masjid.name,
            address=masjid.address,
            admin_region=masjid.admin_region,
            latitude=point.y,
            longitude=point.x,
            status=masjid.status,
            verified=masjid.verified,
            donations_enabled=masjid.donations_enabled,
            timezone=masjid.timezone,
            description=masjid.description,
            suspension_reason=masjid.suspension_reason,
            created_at=masjid.created_at,
            updated_at=masjid.updated_at,
            facilities=(
                FacilitiesResponse.model_validate(masjid.facilities, from_attributes=True)
                if masjid.facilities
                else None
            ),
            contact=(
                ContactResponse.model_validate(masjid.contact, from_attributes=True)
                if masjid.contact
                else None
            ),
            photos=[
                PhotoResponse.model_validate(p, from_attributes=True)
                for p in (masjid.photos or [])
            ],
        )

    # ── Public reads ───────────────────────────────────────────────────────────

    async def get_by_id(self, masjid_id: uuid.UUID) -> MasjidResponse:
        masjid = await self.repo.get_by_id_with_relations(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Masjid not found",
            )
        return self._to_response(masjid)

    async def get_nearby(
        self,
        lat: float,
        lng: float,
        radius_m: float,
        *,
        has_parking: bool | None = None,
        has_sisters_section: bool | None = None,
        has_wheelchair_access: bool | None = None,
        has_wudu_area: bool | None = None,
        has_janazah: bool | None = None,
        has_school: bool | None = None,
    ) -> list[MasjidNearbyResult]:
        pairs = await self.repo.get_nearby(
            lat=lat, lng=lng, radius_m=radius_m,
            has_parking=has_parking,
            has_sisters_section=has_sisters_section,
            has_wheelchair_access=has_wheelchair_access,
            has_wudu_area=has_wudu_area,
            has_janazah=has_janazah,
            has_school=has_school,
        )
        results = []
        for masjid, dist in pairs:
            summary = MasjidSummary.model_validate(masjid, from_attributes=True)
            results.append(MasjidNearbyResult(**summary.model_dump(), distance_m=dist))
        return results

    async def get_stats(self) -> dict:
        return await self.repo.get_stats()

    async def search(self, q: str) -> list[MasjidSummary]:
        if len(q.strip()) < 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Search query must be at least 2 characters",
            )
        masjids = await self.repo.search(q.strip())
        return [MasjidSummary.model_validate(m, from_attributes=True) for m in masjids]

    # ── Admin writes ───────────────────────────────────────────────────────────

    async def create(self, data: MasjidCreate, user: CurrentUser) -> MasjidResponse:
        masjid = await self.repo.create(
            name=data.name,
            address=data.address,
            admin_region=data.admin_region,
            lat=data.latitude,
            lng=data.longitude,
            timezone=data.timezone,
            description=data.description,
        )
        await self.audit.log(
            admin_id=user.user_id, admin_email=user.email, admin_role=user.role,
            action="create_masjid", target_entity="masjid", target_id=masjid.masjid_id,
        )
        await self.repo.commit()
        masjid = await self.repo.get_by_id_with_relations(masjid.masjid_id)
        logger.info("Masjid created", extra={"masjid_id": str(masjid.masjid_id)})
        return self._to_response(masjid)

    async def list_for_admin(
        self,
        *,
        status_filter: str | None,
        admin_region: str | None,
        verified: bool | None,
        q: str | None,
        page: int,
        page_size: int,
    ) -> MasjidAdminListResponse:
        rows, total = await self.repo.list_for_admin(
            status=status_filter,
            admin_region=admin_region,
            verified=verified,
            q=q,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return MasjidAdminListResponse(
            items=[MasjidSummary.model_validate(m, from_attributes=True) for m in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def update(
        self,
        masjid_id: uuid.UUID,
        data: MasjidUpdate,
        user: CurrentUser,
    ) -> MasjidResponse:
        self._check_scope(user, masjid_id)
        masjid = await self._get_or_404(masjid_id)

        if not user.is_platform_admin and data.status is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only platform admins may change masjid status",
            )

        # Separate nested updates from core fields
        raw = data.model_dump(exclude_unset=True)
        facilities_data = None
        contact_data = None
        if "facilities" in raw:
            facilities_data = raw.pop("facilities") or {}
        if "contact" in raw:
            contact_data = raw.pop("contact") or {}

        if raw:
            await self.repo.update_fields(masjid, raw)

        # Sequential (same session — must not use asyncio.gather per CLAUDE.md rule 5)
        if facilities_data:
            await self.repo.update_facilities(masjid_id, facilities_data)
        if contact_data:
            await self.repo.update_contact(masjid_id, contact_data)

        await self.repo.commit()
        masjid = await self.repo.get_by_id_with_relations(masjid_id)
        return self._to_response(masjid)

    async def verify(self, masjid_id: uuid.UUID, user: CurrentUser) -> MasjidResponse:
        masjid = await self._get_or_404(masjid_id)
        if masjid.status != MasjidStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only active masjids can be verified",
            )
        await self.repo.set_verified(masjid, True)
        await self.audit.log(
            admin_id=user.user_id, admin_email=user.email, admin_role=user.role,
            action="verify_masjid", target_entity="masjid", target_id=masjid_id,
        )
        await self.repo.commit()
        masjid = await self.repo.get_by_id_with_relations(masjid_id)
        return self._to_response(masjid)

    async def suspend(
        self, masjid_id: uuid.UUID, reason: str, user: CurrentUser
    ) -> MasjidResponse:
        masjid = await self._get_or_404(masjid_id)
        if masjid.status == MasjidStatus.REMOVED:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot suspend a removed masjid",
            )
        await self.repo.set_status(masjid, MasjidStatus.SUSPENDED, reason)
        await self.audit.log(
            admin_id=user.user_id, admin_email=user.email, admin_role=user.role,
            action="suspend_masjid", target_entity="masjid", target_id=masjid_id,
        )
        await self.repo.commit()
        masjid = await self.repo.get_by_id_with_relations(masjid_id)
        return self._to_response(masjid)
