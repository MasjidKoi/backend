import uuid

from sqlalchemy import and_, func, select

from app.models.masjid_report import MasjidReport
from app.repositories.base import BaseRepository


class MasjidReportRepository(BaseRepository[MasjidReport]):
    model = MasjidReport

    async def list(
        self,
        *,
        status: str | None = None,
        masjid_id: uuid.UUID | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[MasjidReport], int]:
        filters = []
        if status:
            filters.append(MasjidReport.status == status)
        if masjid_id is not None:
            filters.append(MasjidReport.masjid_id == masjid_id)

        where = and_(*filters) if filters else None
        count_stmt = select(func.count()).select_from(MasjidReport)
        data_stmt = select(MasjidReport).order_by(MasjidReport.created_at.desc())
        if where is not None:
            count_stmt = count_stmt.where(where)
            data_stmt = data_stmt.where(where)

        total: int = (await self.db.execute(count_stmt)).scalar_one()
        rows = list(
            (await self.db.execute(data_stmt.offset(offset).limit(limit)))
            .scalars()
            .all()
        )
        return rows, total

    async def get_report_by_id(self, report_id: uuid.UUID) -> MasjidReport | None:
        result = await self.db.execute(
            select(MasjidReport).where(MasjidReport.report_id == report_id)
        )
        return result.scalar_one_or_none()

    async def update_status(
        self, report: MasjidReport, new_status: str
    ) -> MasjidReport:
        report.status = new_status
        await self.db.flush()
        return report

    async def create(
        self,
        *,
        masjid_id: uuid.UUID,
        field_name: str,
        description: str,
        reporter_email: str | None,
    ) -> MasjidReport:
        report = MasjidReport(
            masjid_id=masjid_id,
            field_name=field_name,
            description=description,
            reporter_email=reporter_email,
        )
        self.db.add(report)
        await self.db.flush()
        return report
