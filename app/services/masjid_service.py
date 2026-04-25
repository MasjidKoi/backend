import asyncio
import csv
import io
import logging
import uuid as uuid_lib
import uuid
from dataclasses import dataclass
from datetime import date

import openpyxl
from fastapi import HTTPException, UploadFile, status
from geoalchemy2.shape import to_shape
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import CurrentUser
from app.models.enums import MasjidStatus
from app.models.masjid import Masjid
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.masjid_repository import MasjidRepository
from app.schemas.masjid import (
    BulkImportResponse,
    BulkImportRowError,
    ContactResponse,
    FacilitiesResponse,
    MasjidAdminListResponse,
    MasjidCreate,
    MasjidMergeRequest,
    MasjidNearbyResult,
    MasjidResponse,
    MasjidSummary,
    MasjidUpdate,
    PhotoResponse,
)
from app.services.storage import StorageService

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_ROWS = 1000
_BATCH_SIZE = 100
_REQUIRED_COLS = {"name", "address", "admin_region", "lat", "lng"}
_OPTIONAL_COLS = {"timezone", "description", "donations_enabled"}
_MERGEABLE_FIELDS = frozenset(
    {"name", "address", "admin_region", "timezone", "description", "donations_enabled"}
)
_CSV_HEADERS = [
    "masjid_id",
    "name",
    "address",
    "admin_region",
    "lat",
    "lng",
    "status",
    "verified",
    "donations_enabled",
    "timezone",
    "description",
    # Facilities
    "has_sisters_section",
    "has_wudu_area",
    "has_wudu_male",
    "has_wudu_female",
    "has_wheelchair_access",
    "has_parking",
    "parking_capacity",
    "has_janazah",
    "has_school",
    "imam_name",
    # Contact
    "phone",
    "email",
    "whatsapp",
    "website_url",
    "created_at",
]
_ALLOWED_EXTENSIONS = {"csv", "xlsx"}
_ALLOWED_CONTENT_TYPES = {
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
    "application/zip",
}


@dataclass
class ExportResult:
    data: bytes
    content_type: str
    filename: str


def _parse_csv(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def _parse_xlsx(raw: bytes) -> list[dict]:
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []
    headers = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
    return [
        {headers[j]: (cell if cell is not None else "") for j, cell in enumerate(row)}
        for row in rows[1:]
    ]


def _validate_row(row: dict, row_number: int) -> dict:
    row = {
        k.strip().lower(): (str(v).strip() if v is not None else "")
        for k, v in row.items()
    }
    missing = _REQUIRED_COLS - set(row.keys())
    if missing:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")
    name = row.get("name", "")
    if not name:
        raise ValueError("missing name")
    if len(name) > 200:
        raise ValueError("name exceeds 200 characters")
    address = row.get("address", "")
    if not address:
        raise ValueError("missing address")
    admin_region = row.get("admin_region", "")
    if not admin_region:
        raise ValueError("missing admin_region")
    if len(admin_region) > 100:
        raise ValueError("admin_region exceeds 100 characters")
    try:
        lat = float(row["lat"])
    except (ValueError, KeyError):
        raise ValueError("lat must be a valid float")
    if not (-90.0 <= lat <= 90.0):
        raise ValueError("lat out of range [-90, 90]")
    try:
        lng = float(row["lng"])
    except (ValueError, KeyError):
        raise ValueError("lng must be a valid float")
    if not (-180.0 <= lng <= 180.0):
        raise ValueError("lng out of range [-180, 180]")
    timezone = row.get("timezone", "Asia/Dhaka") or "Asia/Dhaka"
    description = row.get("description") or None
    donations_enabled = row.get("donations_enabled", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    return {
        "name": name,
        "address": address,
        "admin_region": admin_region,
        "lat": lat,
        "lng": lng,
        "timezone": timezone,
        "description": description,
        "donations_enabled": donations_enabled,
    }


def _build_csv(masjids: list) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=_CSV_HEADERS)
    writer.writeheader()
    for m in masjids:
        point = to_shape(m.location)
        fac = m.facilities
        con = m.contact
        writer.writerow(
            {
                "masjid_id": str(m.masjid_id),
                "name": m.name,
                "address": m.address,
                "admin_region": m.admin_region,
                "lat": point.y,
                "lng": point.x,
                "status": m.status,
                "verified": m.verified,
                "donations_enabled": m.donations_enabled,
                "timezone": m.timezone,
                "description": m.description or "",
                # Facilities (None-safe)
                "has_sisters_section": fac.has_sisters_section if fac else "",
                "has_wudu_area": fac.has_wudu_area if fac else "",
                "has_wudu_male": fac.has_wudu_male if fac else "",
                "has_wudu_female": fac.has_wudu_female if fac else "",
                "has_wheelchair_access": fac.has_wheelchair_access if fac else "",
                "has_parking": fac.has_parking if fac else "",
                "parking_capacity": fac.parking_capacity if fac else "",
                "has_janazah": fac.has_janazah if fac else "",
                "has_school": fac.has_school if fac else "",
                "imam_name": fac.imam_name if fac else "",
                # Contact (None-safe)
                "phone": con.phone if con else "",
                "email": con.email if con else "",
                "whatsapp": con.whatsapp if con else "",
                "website_url": con.website_url if con else "",
                "created_at": m.created_at.isoformat(),
            }
        )
    return output.getvalue()


def _build_pdf(masjids: list) -> bytes:
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), leftMargin=1 * cm, rightMargin=1 * cm
    )
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph("MasjidKoi Directory Export", styles["Title"]))
    story.append(Paragraph(f"Generated: {date.today().isoformat()}", styles["Normal"]))
    story.append(Spacer(1, 0.5 * cm))

    col_headers = [
        "Name",
        "Address",
        "Region",
        "Lat",
        "Lng",
        "Status",
        "Verified",
        "Sisters",
        "Wudu",
        "Parking",
        "Wheelchair",
        "Phone",
        "Created",
    ]
    data = [col_headers]
    for m in masjids:
        point = to_shape(m.location)
        fac = m.facilities
        con = m.contact
        data.append(
            [
                m.name[:35],
                m.address[:45],
                m.admin_region,
                f"{point.y:.4f}",
                f"{point.x:.4f}",
                m.status,
                "Yes" if m.verified else "No",
                "Yes" if (fac and fac.has_sisters_section) else "No",
                "Yes" if (fac and fac.has_wudu_area) else "No",
                "Yes" if (fac and fac.has_parking) else "No",
                "Yes" if (fac and fac.has_wheelchair_access) else "No",
                (con.phone or "") if con else "",
                m.created_at.strftime("%Y-%m-%d"),
            ]
        )

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a5c38")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#f5f5f5")],
                ),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return buf.getvalue()


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
                FacilitiesResponse.model_validate(
                    masjid.facilities, from_attributes=True
                )
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
            lat=lat,
            lng=lng,
            radius_m=radius_m,
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
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="create_masjid",
            target_entity="masjid",
            target_id=masjid.masjid_id,
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
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="verify_masjid",
            target_entity="masjid",
            target_id=masjid_id,
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
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="suspend_masjid",
            target_entity="masjid",
            target_id=masjid_id,
        )
        await self.repo.commit()
        masjid = await self.repo.get_by_id_with_relations(masjid_id)
        return self._to_response(masjid)

    async def merge(
        self, data: MasjidMergeRequest, user: CurrentUser
    ) -> MasjidResponse:
        source = await self.repo.get_by_id(data.source_masjid_id)
        if not source:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Source masjid not found"
            )
        target = await self.repo.get_by_id(data.target_masjid_id)
        if not target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Target masjid not found"
            )

        if source.masjid_id == target.masjid_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Source and target must differ",
            )
        if source.status == MasjidStatus.REMOVED:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Source masjid is already removed",
            )

        if data.copy_fields:
            invalid = set(data.copy_fields) - _MERGEABLE_FIELDS
            if invalid:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Non-mergeable fields: {sorted(invalid)}",
                )

        # Resolve prayer_times unique constraint before reassigning
        deleted_pt = await self.repo.delete_conflicting_prayer_times(
            data.source_masjid_id, data.target_masjid_id
        )

        # Copy useful non-null values from source 1:1 children → target
        src_fac, src_con, src_jum = await self.repo.get_1to1_children(
            data.source_masjid_id
        )
        tgt_fac, tgt_con, tgt_jum = await self.repo.get_1to1_children(
            data.target_masjid_id
        )

        if src_fac and tgt_fac:
            fac_diff: dict = {}
            for col in ("imam_name", "imam_qualifications", "parking_capacity"):
                if getattr(tgt_fac, col) is None and getattr(src_fac, col) is not None:
                    fac_diff[col] = getattr(src_fac, col)
            for col in (
                "has_sisters_section",
                "has_wudu_area",
                "has_wudu_male",
                "has_wudu_female",
                "has_wheelchair_access",
                "has_parking",
                "has_janazah",
                "has_school",
            ):
                if not getattr(tgt_fac, col) and getattr(src_fac, col):
                    fac_diff[col] = True
            if fac_diff:
                await self.repo.update_facilities(data.target_masjid_id, fac_diff)

        if src_con and tgt_con:
            con_diff: dict = {}
            for col in ("phone", "email", "whatsapp", "website_url"):
                if getattr(tgt_con, col) is None and getattr(src_con, col) is not None:
                    con_diff[col] = getattr(src_con, col)
            if con_diff:
                await self.repo.update_contact(data.target_masjid_id, con_diff)

        # Move all 1:many children to target
        await self.repo.reassign_related_records(
            data.source_masjid_id, data.target_masjid_id
        )

        # Optionally overwrite target fields with source values
        if data.copy_fields:
            fields_to_apply = {f: getattr(source, f) for f in data.copy_fields}
            await self.repo.update_fields(target, fields_to_apply)

        await self.repo.set_status(source, MasjidStatus.REMOVED)

        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="merge_masjid",
            target_entity="masjid",
            target_id=data.target_masjid_id,
            details={
                "source_id": str(data.source_masjid_id),
                "target_id": str(data.target_masjid_id),
                "copy_fields": data.copy_fields,
                "conflicting_prayer_times_deleted": deleted_pt,
            },
        )
        await self.repo.commit()
        merged = await self.repo.get_by_id_with_relations(data.target_masjid_id)
        return self._to_response(merged)

    async def bulk_import(
        self,
        file: UploadFile,
        user: CurrentUser,
        storage: StorageService,
        field_map: dict[str, str] | None = None,
    ) -> BulkImportResponse:
        filename = file.filename or "upload"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext not in _ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported file type '.{ext}'. Allowed: csv, xlsx",
            )
        if file.content_type and file.content_type not in _ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported content-type '{file.content_type}'",
            )

        raw = await file.read(_MAX_FILE_BYTES + 1)
        if len(raw) > _MAX_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="File exceeds 10 MB limit",
            )

        import_key = f"{date.today().isoformat()}/{uuid_lib.uuid4()}_{filename}"
        content_type = (
            "text/csv"
            if ext == "csv"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        await storage.upload(
            bucket=settings.S3_BUCKET_IMPORTS,
            key=import_key,
            data=raw,
            content_type=content_type,
        )

        loop = asyncio.get_event_loop()
        try:
            rows = await loop.run_in_executor(
                None, _parse_csv if ext == "csv" else _parse_xlsx, raw
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Could not parse file: {exc}",
            )

        if len(rows) > _MAX_ROWS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"File contains {len(rows)} rows; maximum is {_MAX_ROWS}",
            )

        if field_map:
            allowed_targets = _REQUIRED_COLS | _OPTIONAL_COLS
            invalid = set(field_map.values()) - allowed_targets
            if invalid:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Unknown target field(s) in field_map: {sorted(invalid)}",
                )
            rows = [{field_map.get(k, k): v for k, v in row.items()} for row in rows]

        created = 0
        errors: list[BulkImportRowError] = []

        for i, row in enumerate(rows, start=2):
            try:
                validated = _validate_row(row, i)
            except ValueError as exc:
                errors.append(BulkImportRowError(row=i, reason=str(exc)))
                continue

            try:
                await self.repo.create(
                    name=validated["name"],
                    address=validated["address"],
                    admin_region=validated["admin_region"],
                    lat=validated["lat"],
                    lng=validated["lng"],
                    timezone=validated["timezone"],
                    description=validated.get("description"),
                    donations_enabled=validated["donations_enabled"],
                )
                created += 1
            except IntegrityError as exc:
                logger.warning("Row %d integrity error: %s", i, exc)
                errors.append(
                    BulkImportRowError(row=i, reason="Database constraint violation")
                )
                await self.repo.db.rollback()
                continue
            except Exception as exc:
                logger.warning("Row %d create failed: %s", i, exc)
                errors.append(
                    BulkImportRowError(row=i, reason="Database error creating row")
                )
                await self.repo.db.rollback()
                continue

            if created % _BATCH_SIZE == 0:
                await self.repo.commit()

        if created % _BATCH_SIZE != 0:
            await self.repo.commit()

        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="bulk_import",
            target_entity="masjid",
            details={"filename": filename, "created": created, "failed": len(errors)},
        )
        await self.repo.commit()

        logger.info(
            "Bulk import complete",
            extra={"created": created, "failed": len(errors), "key": import_key},
        )
        return BulkImportResponse(
            created=created,
            failed=len(errors),
            errors=errors,
            import_file_key=import_key,
        )

    async def export(
        self,
        *,
        format: str,
        status_filter: str | None,
        admin_region: str | None,
        verified: bool | None,
    ) -> ExportResult:
        if format not in ("csv", "pdf"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="format must be 'csv' or 'pdf'",
            )

        masjids = await self.repo.list_all_for_export(
            status=status_filter,
            admin_region=admin_region,
            verified=verified,
        )

        loop = asyncio.get_event_loop()
        if format == "csv":
            csv_str = await loop.run_in_executor(None, _build_csv, masjids)
            return ExportResult(
                data=csv_str.encode("utf-8"),
                content_type="text/csv; charset=utf-8",
                filename="masjidkoi_export.csv",
            )
        else:
            pdf_bytes = await loop.run_in_executor(None, _build_pdf, masjids)
            return ExportResult(
                data=pdf_bytes,
                content_type="application/pdf",
                filename="masjidkoi_export.pdf",
            )
