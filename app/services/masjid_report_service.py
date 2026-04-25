import uuid
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.masjid_report_repository import MasjidReportRepository
from app.repositories.masjid_repository import MasjidRepository
from app.schemas.masjid_report import (
    MasjidReportAdminResponse,
    MasjidReportCreate,
    MasjidReportListResponse,
    MasjidReportResponse,
)
from app.services.email_service import send_email


class MasjidReportService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = MasjidReportRepository(db)
        self.masjid_repo = MasjidRepository(db)
        self.audit = AuditLogRepository(db)

    async def create_report(
        self,
        masjid_id: uuid.UUID,
        data: MasjidReportCreate,
    ) -> MasjidReportResponse:
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Masjid not found"
            )

        report = await self.repo.create(
            masjid_id=masjid_id,
            field_name=data.field_name,
            description=data.description,
            reporter_email=str(data.reporter_email) if data.reporter_email else None,
        )
        await self.repo.commit()

        return MasjidReportResponse(
            report_id=report.report_id,
            status=report.status,
            created_at=report.created_at,
        )

    async def list_reports(
        self,
        *,
        status_filter: str | None,
        masjid_id: uuid.UUID | None,
        page: int,
        page_size: int,
    ) -> MasjidReportListResponse:
        rows, total = await self.repo.list(
            status=status_filter,
            masjid_id=masjid_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return MasjidReportListResponse(
            items=[_to_admin_response(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def update_report_status(
        self,
        report_id: uuid.UUID,
        new_status: Literal["reviewed", "resolved"],
        user: CurrentUser,
    ) -> MasjidReportAdminResponse:
        report = await self.repo.get_report_by_id(report_id)
        if not report:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
            )

        await self.repo.update_status(report, new_status)

        if new_status == "resolved" and report.reporter_email:
            await send_email(
                to=report.reporter_email,
                subject="Your MasjidKoi report has been resolved",
                body=(
                    f"Thank you for your report about field '{report.field_name}'.\n\n"
                    "Our team has reviewed the information and updated the masjid profile.\n\n"
                    "JazakAllah Khair,\nThe MasjidKoi Team"
                ),
            )

        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="update_report_status",
            target_entity="masjid_report",
            target_id=report_id,
            details={"new_status": new_status},
        )
        await self.repo.commit()
        return _to_admin_response(report)


def _to_admin_response(report) -> MasjidReportAdminResponse:
    return MasjidReportAdminResponse(
        report_id=report.report_id,
        masjid_id=report.masjid_id,
        field_name=report.field_name,
        description=report.description,
        reporter_email=report.reporter_email,
        status=report.status,
        created_at=report.created_at,
    )
