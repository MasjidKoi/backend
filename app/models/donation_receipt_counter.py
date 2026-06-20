from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DonationReceiptCounter(Base):
    """Gapless per-year receipt sequence for PDF acknowledgments (PRD 05).

    Auditors dislike UUIDs, so every completed donation is stamped with a human
    number like ``MK-2026-000123``. Allocation is a single atomic upsert::

        INSERT INTO donation_receipt_counters (year, last_number)
        VALUES (:year, 1)
        ON CONFLICT (year) DO UPDATE SET last_number = last_number + 1
        RETURNING last_number

    Gapless because the counter only advances inside the completion transaction;
    failed and pending donations never consume a number.
    """

    __tablename__ = "donation_receipt_counters"

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
