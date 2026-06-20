import uuid

from fastapi import APIRouter, Depends, Query, status

from app.core.rate_limit import make_rate_limiter
from app.core.security import CurrentUser
from app.dependencies.auth import get_current_user, require_masjid_admin
from app.dependencies.masjid_question import get_masjid_question_service
from app.schemas.masjid_question import (
    MyQuestionResponse,
    QuestionAnswer,
    QuestionCreate,
    QuestionModerationListResponse,
    QuestionModerationResponse,
    QuestionPublicListResponse,
)
from app.services.masjid_question_service import MasjidQuestionService

# No prefix — full paths declared per-route so consumer / public / admin / me
# routes sit in their natural namespaces (mirrors community_photos.router).
# Path templates carry the static `/questions` segment so they never collide
# with masjids.router's `/masjids/{masjid_id}` routes.
router = APIRouter(tags=["questions"])

# Coarse per-IP guard layered on top of the deterministic per-user/per-masjid
# DB caps inside the service.
_ask_limiter = make_rate_limiter(limit=30, window_s=3600, key_prefix="masjid_question")


# ── Consumer ─────────────────────────────────────────────────────────────────


@router.post(
    "/masjids/{masjid_id}/questions",
    response_model=MyQuestionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ask a masjid a question (authenticated, moderated, rate limited)",
)
async def ask_question(
    masjid_id: uuid.UUID,
    body: QuestionCreate,
    user: CurrentUser = Depends(get_current_user),
    _rl: None = Depends(_ask_limiter),
    service: MasjidQuestionService = Depends(get_masjid_question_service),
) -> MyQuestionResponse:
    return await service.ask(masjid_id, body, user)


# ── Public listing ─────────────────────────────────────────────────────────────


@router.get(
    "/masjids/{masjid_id}/questions",
    response_model=QuestionPublicListResponse,
    summary="List answered questions for a masjid (public)",
)
async def list_questions(
    masjid_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: MasjidQuestionService = Depends(get_masjid_question_service),
) -> QuestionPublicListResponse:
    return await service.list_public(masjid_id, page=page, page_size=page_size)


# ── Asker ──────────────────────────────────────────────────────────────────────


@router.get(
    "/me/questions",
    response_model=list[MyQuestionResponse],
    summary="List my questions with status + timestamps",
)
async def list_my_questions(
    user: CurrentUser = Depends(get_current_user),
    service: MasjidQuestionService = Depends(get_masjid_question_service),
) -> list[MyQuestionResponse]:
    return await service.list_mine(user)


# ── Moderation (masjid admin + platform admin) ──────────────────────────────────


@router.get(
    "/admin/masjids/{masjid_id}/questions",
    response_model=QuestionModerationListResponse,
    summary="Moderation queue of questions for a masjid (masjid_admin)",
)
async def list_questions_for_moderation(
    masjid_id: uuid.UUID,
    question_status: str | None = Query(default="pending", alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidQuestionService = Depends(get_masjid_question_service),
) -> QuestionModerationListResponse:
    return await service.list_for_moderation(
        masjid_id,
        user,
        status_filter=question_status,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/admin/questions/{question_id}/answer",
    response_model=QuestionModerationResponse,
    summary="Answer a question (masjid_admin / platform_admin)",
)
async def answer_question(
    question_id: uuid.UUID,
    body: QuestionAnswer,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidQuestionService = Depends(get_masjid_question_service),
) -> QuestionModerationResponse:
    return await service.answer(question_id, body, user)


@router.post(
    "/admin/questions/{question_id}/reject",
    response_model=QuestionModerationResponse,
    summary="Reject a question — visible to asker only (masjid_admin / platform_admin)",
)
async def reject_question(
    question_id: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidQuestionService = Depends(get_masjid_question_service),
) -> QuestionModerationResponse:
    return await service.reject(question_id, user)
