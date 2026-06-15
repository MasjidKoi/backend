import uuid
from datetime import datetime

from sqlalchemy import func, or_, select

from app.models.enums import QuestionStatus
from app.models.masjid_question import MasjidQuestion
from app.repositories.base import BaseRepository


class MasjidQuestionRepository(BaseRepository[MasjidQuestion]):
    model = MasjidQuestion

    async def create(
        self,
        *,
        masjid_id: uuid.UUID,
        asker_user_id: uuid.UUID,
        question: str,
    ) -> MasjidQuestion:
        row = MasjidQuestion(
            masjid_id=masjid_id,
            asker_user_id=asker_user_id,
            question=question,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def get_by_id(self, question_id: uuid.UUID) -> MasjidQuestion | None:
        result = await self.db.execute(
            select(MasjidQuestion).where(MasjidQuestion.question_id == question_id)
        )
        return result.scalar_one_or_none()

    # ── Rate-cap counts ──────────────────────────────────────────────────────

    async def count_by_user_since(self, user_id: uuid.UUID, since: datetime) -> int:
        """Questions this user asked since `since` (per-user/day cap)."""
        result = await self.db.execute(
            select(func.count())
            .select_from(MasjidQuestion)
            .where(MasjidQuestion.asker_user_id == user_id)
            .where(MasjidQuestion.created_at >= since)
        )
        return result.scalar_one()

    async def count_by_user_masjid_since(
        self, user_id: uuid.UUID, masjid_id: uuid.UUID, since: datetime
    ) -> int:
        """Questions this user asked one masjid since `since` (per-masjid/day cap)."""
        result = await self.db.execute(
            select(func.count())
            .select_from(MasjidQuestion)
            .where(MasjidQuestion.asker_user_id == user_id)
            .where(MasjidQuestion.masjid_id == masjid_id)
            .where(MasjidQuestion.created_at >= since)
        )
        return result.scalar_one()

    # ── Listings ─────────────────────────────────────────────────────────────

    async def list_answered_public(
        self, masjid_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[list[MasjidQuestion], int]:
        """Public listing of answered questions for one masjid (newest answer first)."""
        base = (
            select(MasjidQuestion)
            .where(MasjidQuestion.masjid_id == masjid_id)
            .where(MasjidQuestion.status == QuestionStatus.ANSWERED)
        )
        total: int = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = list(
            (
                await self.db.execute(
                    base.order_by(MasjidQuestion.answered_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return rows, total

    async def list_for_masjid_moderation(
        self,
        masjid_id: uuid.UUID,
        *,
        status: str | None,
        offset: int,
        limit: int,
        ngo_overdue_before: datetime | None = None,
    ) -> tuple[list[MasjidQuestion], int]:
        """Moderation queue for a masjid (any/filtered status, oldest first).

        ``ngo_overdue_before`` restricts the queue to the NGO-visible slice of a
        *claimed* masjid (Gap #10): pending rows only when overdue past the SLA,
        plus all resolved history. Left unset for the masjid admin's own queue.
        """
        base = select(MasjidQuestion).where(MasjidQuestion.masjid_id == masjid_id)
        if status:
            base = base.where(MasjidQuestion.status == status)
        if ngo_overdue_before is not None:
            base = base.where(
                or_(
                    MasjidQuestion.status != QuestionStatus.PENDING,
                    MasjidQuestion.created_at <= ngo_overdue_before,
                )
            )
        total: int = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = list(
            (
                await self.db.execute(
                    base.order_by(MasjidQuestion.created_at.asc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return rows, total

    async def list_for_user(
        self, user_id: uuid.UUID, limit: int = 50
    ) -> list[MasjidQuestion]:
        """The asker's own questions (GET /me/questions)."""
        result = await self.db.execute(
            select(MasjidQuestion)
            .where(MasjidQuestion.asker_user_id == user_id)
            .order_by(MasjidQuestion.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # ── Mutations ────────────────────────────────────────────────────────────

    async def set_answer(
        self,
        question: MasjidQuestion,
        *,
        answer: str,
        answered_by: uuid.UUID,
        author_role: str,
    ) -> MasjidQuestion:
        question.answer = answer
        question.answered_by = answered_by
        question.answer_author_role = author_role
        question.answered_at = func.now()
        question.status = QuestionStatus.ANSWERED
        await self.db.flush()
        return question

    async def set_status(
        self, question: MasjidQuestion, new_status: str
    ) -> MasjidQuestion:
        question.status = new_status
        await self.db.flush()
        return question
