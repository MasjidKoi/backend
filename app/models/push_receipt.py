from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PushReceipt(Base):
    """A pending Expo delivery receipt awaiting the async getReceipts poll
    (PRD 03 #0 follow-up).

    Some delivery failures — notably ``DeviceNotRegistered`` — only surface in
    Expo's *receipts* a few minutes after the send, not in the synchronous
    ticket. On every real send we record ``(receipt_id, token)`` here; a
    scheduled job polls receipts older than ~15 min, prunes the tokens Expo
    reports dead, and deletes the processed rows. Rows are transient — they exist
    only between a send and its receipt check. ``token`` carries no FK (it mirrors
    ``device_tokens.token``, which may already be gone by poll time).
    """

    __tablename__ = "push_receipts"
    __table_args__ = (Index("ix_push_receipts_created", "created_at"),)

    # The Expo ticket id is globally unique, so it is the natural primary key.
    receipt_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    token: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
