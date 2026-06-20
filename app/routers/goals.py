import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, status

from app.core.security import CurrentUser
from app.dependencies.auth import get_current_user
from app.dependencies.goal import get_goal_service
from app.schemas.goal import (
    GoalCompletionCreate,
    GoalCreate,
    GoalListResponse,
    GoalResponse,
    GoalStatusUpdate,
    GoalTemplateCreate,
)
from app.services.goal_service import GoalService

router = APIRouter(prefix="/users/me/goals", tags=["gamification"])


@router.post(
    "",
    response_model=GoalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a free-form goal (quran_quantity or recurring)",
)
async def create_goal(
    body: GoalCreate,
    user: CurrentUser = Depends(get_current_user),
    service: GoalService = Depends(get_goal_service),
) -> GoalResponse:
    return await service.create_goal(body, user)


@router.post(
    "/templates",
    response_model=GoalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Instantiate a preset goal template (Khatm / Ayat al-Kursi / al-Kahf)",
)
async def create_goal_from_template(
    body: GoalTemplateCreate,
    user: CurrentUser = Depends(get_current_user),
    service: GoalService = Depends(get_goal_service),
) -> GoalResponse:
    return await service.create_from_template(body, user)


@router.get(
    "",
    response_model=GoalListResponse,
    summary="List my goals with computed progress",
)
async def list_goals(
    status_filter: str | None = Query(default=None, alias="status"),
    user: CurrentUser = Depends(get_current_user),
    service: GoalService = Depends(get_goal_service),
) -> GoalListResponse:
    return await service.list_goals(user, status_filter)


@router.get(
    "/{goal_id}",
    response_model=GoalResponse,
    summary="Get a single goal with computed progress",
)
async def get_goal(
    goal_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    service: GoalService = Depends(get_goal_service),
) -> GoalResponse:
    return await service.get_goal(goal_id, user)


@router.patch(
    "/{goal_id}",
    response_model=GoalResponse,
    summary="Pause, resume, abandon, or rename a goal",
)
async def update_goal(
    goal_id: uuid.UUID,
    body: GoalStatusUpdate,
    user: CurrentUser = Depends(get_current_user),
    service: GoalService = Depends(get_goal_service),
) -> GoalResponse:
    return await service.update_goal(goal_id, body, user)


@router.delete(
    "/{goal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a goal and its check-offs",
)
async def delete_goal(
    goal_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    service: GoalService = Depends(get_goal_service),
) -> None:
    await service.delete_goal(goal_id, user)


@router.post(
    "/{goal_id}/completions",
    response_model=GoalResponse,
    summary="Check off a recurring goal for a date (idempotent)",
)
async def complete_goal(
    goal_id: uuid.UUID,
    body: GoalCompletionCreate,
    user: CurrentUser = Depends(get_current_user),
    service: GoalService = Depends(get_goal_service),
) -> GoalResponse:
    return await service.complete_goal(goal_id, body, user)


@router.delete(
    "/{goal_id}/completions/{completion_date}",
    response_model=GoalResponse,
    summary="Un-check a recurring goal's completion for a date",
)
async def uncomplete_goal(
    goal_id: uuid.UUID,
    completion_date: date,
    user: CurrentUser = Depends(get_current_user),
    service: GoalService = Depends(get_goal_service),
) -> GoalResponse:
    return await service.uncomplete_goal(goal_id, completion_date, user)
