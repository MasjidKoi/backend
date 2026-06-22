import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.models.enums import PushMessageType, QuestionStatus
from app.models.masjid_question import MasjidQuestion
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.co_admin_invite_repository import CoAdminInviteRepository
from app.repositories.masjid_question_repository import MasjidQuestionRepository
from app.repositories.masjid_repository import MasjidRepository
from app.schemas.masjid_question import (
    MyQuestionResponse,
    QuestionAnswer,
    QuestionCreate,
    QuestionModerationListResponse,
    QuestionModerationResponse,
    QuestionPublic,
    QuestionPublicListResponse,
)
from app.services.moderation_routing import (
    can_moderate,
    ngo_pending_cutoff,
)
from app.services.push_service import PushMessage, PushService

logger = logging.getLogger(__name__)

# Abuse guards enforced in the DB (deterministic, restart-safe) — distinct from
# the coarse per-IP Redis limiter on the route.
_MAX_PER_USER_PER_DAY = 10
_MAX_PER_USER_PER_MASJID_PER_DAY = 3
_WINDOW = timedelta(hours=24)


class MasjidQuestionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = MasjidQuestionRepository(db)
        self.masjid_repo = MasjidRepository(db)
        self.co_admin = CoAdminInviteRepository(db)
        self.audit = AuditLogRepository(db)

    # ── Internal: moderation routing (Gap #10, shared with community photos) ──

    async def _ngo_cutoff_for_list(
        self, user: CurrentUser, masjid_id: uuid.UUID
    ) -> datetime | None:
        """Authorise the caller for this masjid's moderation queue and return the
        NGO overdue-cutoff to apply (None = unrestricted full queue)."""
        if user.is_platform_admin:
            # Claimed masjid → the NGO sees only the overdue/resolved slice.
            # Unclaimed → the NGO owns the whole queue.
            if await self.co_admin.masjid_has_claimed_admin(masjid_id):
                return ngo_pending_cutoff(datetime.now(timezone.utc))
            return None
        if user.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to your own masjid",
            )
        return None

    async def _authorize_action(
        self, user: CurrentUser, question: MasjidQuestion
    ) -> None:
        """Authorise a moderation action on one item via the shared predicate."""
        has_claimed = await self.co_admin.masjid_has_claimed_admin(question.masjid_id)
        if not can_moderate(
            is_platform_admin=user.is_platform_admin,
            user_masjid_id=user.masjid_id,
            item_masjid_id=question.masjid_id,
            masjid_has_claimed_admin=has_claimed,
            pending_since=question.created_at,
            now=datetime.now(timezone.utc),
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to moderate this question",
            )

    async def _get_or_404(self, question_id: uuid.UUID) -> MasjidQuestion:
        question = await self.repo.get_by_id(question_id)
        if not question:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Question not found",
            )
        return question

    # ── Consumer ─────────────────────────────────────────────────────────────

    async def ask(
        self, masjid_id: uuid.UUID, data: QuestionCreate, user: CurrentUser
    ) -> MyQuestionResponse:
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Masjid not found"
            )

        since = datetime.now(timezone.utc) - _WINDOW
        per_masjid = await self.repo.count_by_user_masjid_since(
            user.user_id, masjid_id, since
        )
        if per_masjid >= _MAX_PER_USER_PER_MASJID_PER_DAY:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Question limit reached — max "
                    f"{_MAX_PER_USER_PER_MASJID_PER_DAY} questions per masjid per day"
                ),
            )
        per_user = await self.repo.count_by_user_since(user.user_id, since)
        if per_user >= _MAX_PER_USER_PER_DAY:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Question limit reached — max {_MAX_PER_USER_PER_DAY} "
                    "questions per day"
                ),
            )

        question = await self.repo.create(
            masjid_id=masjid_id,
            asker_user_id=user.user_id,
            question=data.question.strip(),
        )
        await self.repo.commit()
        await self.repo.refresh(question)
        logger.info(
            "Masjid question asked",
            extra={
                "question_id": str(question.question_id),
                "masjid_id": str(masjid_id),
                "user_id": str(user.user_id),
            },
        )
        return MyQuestionResponse.model_validate(question)

    async def list_mine(self, user: CurrentUser) -> list[MyQuestionResponse]:
        rows = await self.repo.list_for_user(user.user_id)
        names = await self.masjid_repo.names_by_ids([r.masjid_id for r in rows])
        out: list[MyQuestionResponse] = []
        for r in rows:
            resp = MyQuestionResponse.model_validate(r)
            resp.masjid_name = names.get(r.masjid_id)
            out.append(resp)
        return out

    # ── Public listing ─────────────────────────────────────────────────────────

    async def list_public(
        self, masjid_id: uuid.UUID, *, page: int, page_size: int
    ) -> QuestionPublicListResponse:
        rows, total = await self.repo.list_answered_public(
            masjid_id, offset=(page - 1) * page_size, limit=page_size
        )
        return QuestionPublicListResponse(
            items=[QuestionPublic.model_validate(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    # ── Moderation ─────────────────────────────────────────────────────────────

    async def list_for_moderation(
        self,
        masjid_id: uuid.UUID,
        user: CurrentUser,
        *,
        status_filter: str | None,
        page: int,
        page_size: int,
    ) -> QuestionModerationListResponse:
        ngo_overdue_before = await self._ngo_cutoff_for_list(user, masjid_id)
        rows, total = await self.repo.list_for_masjid_moderation(
            masjid_id,
            status=status_filter,
            offset=(page - 1) * page_size,
            limit=page_size,
            ngo_overdue_before=ngo_overdue_before,
        )
        return QuestionModerationListResponse(
            items=[QuestionModerationResponse.model_validate(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def answer(
        self, question_id: uuid.UUID, data: QuestionAnswer, user: CurrentUser
    ) -> QuestionModerationResponse:
        question = await self._get_or_404(question_id)
        await self._authorize_action(user, question)
        if question.status != QuestionStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Question already {question.status}",
            )
        await self.repo.set_answer(
            question,
            answer=data.answer.strip(),
            answered_by=user.user_id,
            author_role=str(user.role),
        )
        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="answer_masjid_question",
            target_entity="masjid_question",
            target_id=question_id,
            details={"masjid_id": str(question.masjid_id)},
        )
        await self.repo.commit()
        await self.repo.refresh(question)
        # Notify the asker their question was answered, deep-linking to it
        # (best-effort — a push failure must never undo the answer).
        await PushService(self.db).notify_users(
            [question.asker_user_id],
            PushMessage(
                message_type=PushMessageType.QNA_ANSWERED,
                title="Your question was answered",
                body="The masjid has answered your question.",
                data={
                    "masjid_id": str(question.masjid_id),
                    "question_id": str(question_id),
                },
            ),
        )
        logger.info("Masjid question answered", extra={"question_id": str(question_id)})
        return QuestionModerationResponse.model_validate(question)

    async def reject(
        self, question_id: uuid.UUID, user: CurrentUser
    ) -> QuestionModerationResponse:
        question = await self._get_or_404(question_id)
        await self._authorize_action(user, question)
        if question.status != QuestionStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Question already {question.status}",
            )
        await self.repo.set_status(question, QuestionStatus.REJECTED)
        await self.audit.log(
            admin_id=user.user_id,
            admin_email=user.email,
            admin_role=user.role,
            action="reject_masjid_question",
            target_entity="masjid_question",
            target_id=question_id,
            details={"masjid_id": str(question.masjid_id)},
        )
        await self.repo.commit()
        await self.repo.refresh(question)
        # Reject is visible to the asker only — no public trace, no push.
        logger.info("Masjid question rejected", extra={"question_id": str(question_id)})
        return QuestionModerationResponse.model_validate(question)
