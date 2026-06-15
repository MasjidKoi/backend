import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import QuestionStatus


class MasjidQuestion(Base):
    """A visitor question asked of a masjid (PRD 04, "Ask-the-masjid").

    A question is born ``pending`` and travels a ``pending → answered | rejected``
    lifecycle. Only ``answered`` questions ever surface publicly — a profile must
    never read "14 questions · 0 answers". ``rejected`` questions stay visible to
    their asker only, with no public trace and no push.

    The answerer's identity (``answered_by``) and role (``answer_author_role``)
    are stored explicitly so community-authored answers can open later without a
    schema change; today only the masjid's own admin or a platform admin answer.
    """

    __tablename__ = "masjid_questions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','answered','rejected')",
            name="ck_masjid_questions_status",
        ),
        # Public answered-only listing + admin moderation-queue reads.
        Index("ix_masjid_questions_masjid_status", "masjid_id", "status"),
        # Backs GET /me/questions.
        Index("ix_masjid_questions_asker", "asker_user_id"),
    )

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    masjid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("masjids.masjid_id", ondelete="CASCADE"),
        nullable=False,
    )
    # No FK — users live in GoTrue's auth schema, not a local table.
    asker_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=QuestionStatus.PENDING
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # No FK (GoTrue auth schema). Nullable so the answerer's account can be removed
    # without dropping the answer.
    answered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    answer_author_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    masjid: Mapped["Masjid"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Masjid",
        lazy="raise",
    )
