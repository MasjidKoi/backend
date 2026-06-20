from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.donation_service import DonationService
from app.services.receipt_service import ReceiptService
from app.services.recurring_schedule_service import RecurringScheduleService


def get_donation_service(db: AsyncSession = Depends(get_db)) -> DonationService:
    """DonationService scoped to the request's session, using the real
    SSLCommerz gateway singleton."""
    return DonationService(db)


def get_recurring_schedule_service(
    db: AsyncSession = Depends(get_db),
) -> RecurringScheduleService:
    return RecurringScheduleService(db)


def get_receipt_service(db: AsyncSession = Depends(get_db)) -> ReceiptService:
    return ReceiptService(db)
