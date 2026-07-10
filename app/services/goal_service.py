import uuid
from dataclasses import asdict
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.core.time import DHAKA_TZ
from app.models.enums import GoalKind, GoalStatus
from app.models.user_goal import UserGoal
from app.repositories.user_goal_repository import UserGoalRepository
from app.repositories.user_journal_repository import UserJournalRepository
from app.schemas.goal import (
    GoalCompletionCreate,
    GoalCreate,
    GoalListResponse,
    GoalProgress,
    GoalResponse,
    GoalStatusUpdate,
    GoalTemplateCreate,
)
from app.services.goal_progress import (
    compute_quran_progress,
    compute_recurring_progress,
)
from app.services.goal_templates import GOAL_TEMPLATES


class GoalService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = UserGoalRepository(db)
        self.journal_repo = UserJournalRepository(db)

    @staticmethod
    def _today() -> date:
        return datetime.now(timezone.utc).astimezone(DHAKA_TZ).date()

    # ── Create ────────────────────────────────────────────────────────────────

    async def create_goal(self, data: GoalCreate, user: CurrentUser) -> GoalResponse:
        goal = UserGoal(
            user_id=user.user_id,
            goal_kind=data.goal_kind.value,
            template=None,
            title=data.title,
            status=GoalStatus.ACTIVE.value,
            target_amount=data.target_amount,
            unit=data.unit.value if data.unit else None,
            start_date=data.start_date,
            end_date=data.end_date,
            recurrence=data.recurrence.value if data.recurrence else None,
        )
        return await self._persist_and_respond(goal, user)

    async def create_from_template(
        self, data: GoalTemplateCreate, user: CurrentUser
    ) -> GoalResponse:
        template = GOAL_TEMPLATES[data.template]
        if template.requires_date_range and (
            data.start_date is None or data.end_date is None
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Template '{template.key}' requires start_date and end_date",
            )

        goal = UserGoal(
            user_id=user.user_id,
            goal_kind=template.goal_kind.value,
            template=template.key,
            title=template.title,
            status=GoalStatus.ACTIVE.value,
            target_amount=template.target_amount,
            unit=template.unit,
            # Dates apply only to a date-bound (quantity) template.
            start_date=data.start_date if template.requires_date_range else None,
            end_date=data.end_date if template.requires_date_range else None,
            recurrence=template.recurrence.value if template.recurrence else None,
        )
        return await self._persist_and_respond(goal, user)

    # ── Read ──────────────────────────────────────────────────────────────────

    async def list_goals(
        self, user: CurrentUser, status_filter: str | None
    ) -> GoalListResponse:
        goals = await self.repo.list_for_user(user.user_id, status=status_filter)
        items = await self._build_responses(goals, user)
        return GoalListResponse(items=items, total=len(items))

    async def get_goal(self, goal_id: uuid.UUID, user: CurrentUser) -> GoalResponse:
        goal = await self._get_owned_or_404(goal_id, user)
        return await self._build_response(goal, user)

    # ── Update / delete ─────────────────────────────────────────────────────--

    async def update_goal(
        self, goal_id: uuid.UUID, data: GoalStatusUpdate, user: CurrentUser
    ) -> GoalResponse:
        goal = await self._get_owned_or_404(goal_id, user)
        if data.status is not None:
            goal.status = data.status.value
        if data.title is not None:
            goal.title = data.title
        await self.repo.commit()
        await self.repo.refresh(goal)
        return await self._build_response(goal, user)

    async def delete_goal(self, goal_id: uuid.UUID, user: CurrentUser) -> None:
        goal = await self._get_owned_or_404(goal_id, user)
        await self.repo.db.delete(goal)  # completions cascade
        await self.repo.commit()

    # ── Recurring check-off ─────────────────────────────────────────────────--

    async def complete_goal(
        self, goal_id: uuid.UUID, data: GoalCompletionCreate, user: CurrentUser
    ) -> GoalResponse:
        goal = await self._get_owned_or_404(goal_id, user)
        self._require_recurring(goal)
        today = self._today()
        when = data.completion_date or today
        if when > today:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot check off a goal for a future date.",
            )

        # Idempotent: a second tap on the same date is a no-op, not a 409.
        try:
            async with self.repo.db.begin_nested():
                await self.repo.add_completion(goal_id, when)
        except IntegrityError:
            pass
        await self.repo.commit()
        return await self._build_response(goal, user)

    async def uncomplete_goal(
        self, goal_id: uuid.UUID, completion_date: date, user: CurrentUser
    ) -> GoalResponse:
        goal = await self._get_owned_or_404(goal_id, user)
        self._require_recurring(goal)
        await self.repo.delete_completion(goal_id, completion_date)
        await self.repo.commit()
        return await self._build_response(goal, user)

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _persist_and_respond(
        self, goal: UserGoal, user: CurrentUser
    ) -> GoalResponse:
        """Insert a freshly-built goal and return its response with progress."""
        await self.repo.add(goal)
        await self.repo.commit()
        await self.repo.refresh(goal)
        return await self._build_response(goal, user)

    async def _get_owned_or_404(
        self, goal_id: uuid.UUID, user: CurrentUser
    ) -> UserGoal:
        goal = await self.repo.get_for_user(goal_id, user.user_id)
        if goal is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found"
            )
        return goal

    @staticmethod
    def _require_recurring(goal: UserGoal) -> None:
        if goal.goal_kind != GoalKind.RECURRING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Check-off applies only to recurring goals; quran_quantity "
                    "progress is fed automatically from journal entries."
                ),
            )

    async def _build_response(self, goal: UserGoal, user: CurrentUser) -> GoalResponse:
        today = self._today()
        if goal.goal_kind == GoalKind.QURAN_QUANTITY:
            current = await self.journal_repo.sum_quran_amount(
                user.user_id, goal.unit, goal.start_date, goal.end_date
            )
            progress = self._quran_progress(goal, current, today)
        else:
            dates = await self.repo.completion_dates(goal.goal_id)
            progress = self._recurring_progress(goal, dates, today)
        return self._to_response(goal, progress)

    async def _build_responses(
        self, goals: list[UserGoal], user: CurrentUser
    ) -> list[GoalResponse]:
        """Batched equivalent of ``_build_response`` for a list of goals.

        Progress inputs are fetched in at most two queries total — one grouped
        completion-dates query for recurring goals and one grouped Qur'an
        daily-sums query — then progress is computed in memory, avoiding the
        1 + N round-trips a per-goal build would incur."""
        today = self._today()
        quran_goals = [g for g in goals if g.goal_kind == GoalKind.QURAN_QUANTITY]
        recurring_goals = [g for g in goals if g.goal_kind != GoalKind.QURAN_QUANTITY]

        daily_sums: dict[tuple[str, date], int] = {}
        if quran_goals:
            date_from = min(g.start_date for g in quran_goals)
            date_to = max(g.end_date for g in quran_goals)
            daily_sums = await self.journal_repo.quran_daily_sums(
                user.user_id, date_from, date_to
            )

        completions = await self.repo.completion_dates_for_goals(
            [g.goal_id for g in recurring_goals]
        )

        items: list[GoalResponse] = []
        for goal in goals:
            if goal.goal_kind == GoalKind.QURAN_QUANTITY:
                current = sum(
                    amt
                    for (unit, day), amt in daily_sums.items()
                    if unit == goal.unit and goal.start_date <= day <= goal.end_date
                )
                progress = self._quran_progress(goal, current, today)
            else:
                progress = self._recurring_progress(
                    goal, completions.get(goal.goal_id, set()), today
                )
            items.append(self._to_response(goal, progress))
        return items

    def _quran_progress(
        self, goal: UserGoal, current: int, today: date
    ) -> GoalProgress:
        p = compute_quran_progress(goal.target_amount, current, today, goal.end_date)
        return GoalProgress(
            kind=goal.goal_kind, target_amount=goal.target_amount, **asdict(p)
        )

    def _recurring_progress(
        self, goal: UserGoal, dates: set[date], today: date
    ) -> GoalProgress:
        r = compute_recurring_progress(goal.recurrence, dates, today)
        return GoalProgress(kind=goal.goal_kind, **asdict(r))

    @staticmethod
    def _to_response(goal: UserGoal, progress: GoalProgress) -> GoalResponse:
        return GoalResponse(
            goal_id=goal.goal_id,
            goal_kind=goal.goal_kind,
            template=goal.template,
            title=goal.title,
            status=goal.status,
            target_amount=goal.target_amount,
            unit=goal.unit,
            start_date=goal.start_date,
            end_date=goal.end_date,
            recurrence=goal.recurrence,
            created_at=goal.created_at,
            updated_at=goal.updated_at,
            progress=progress,
        )
