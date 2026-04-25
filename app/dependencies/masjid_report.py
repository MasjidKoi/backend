from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.masjid_report_service import MasjidReportService


def get_masjid_report_service(
    db: AsyncSession = Depends(get_db),
) -> MasjidReportService:
    return MasjidReportService(db)
