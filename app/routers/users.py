import io
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.security import CurrentUser
from app.dependencies.auth import get_current_user
from app.dependencies.storage import get_storage_service
from app.dependencies.user import get_user_service
from app.schemas.user import FavouriteMasjidResponse, MadhabhType, UserProfileResponse
from app.services.storage import StorageService
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/me",
    response_model=UserProfileResponse,
    summary="Get own profile (authenticated)",
)
async def get_me(
    user: CurrentUser = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> UserProfileResponse:
    return await service.get_me(user)


@router.patch(
    "/me",
    response_model=UserProfileResponse,
    summary="Update name, avatar photo, or madhab preference",
)
async def update_me(
    display_name: str | None = Form(default=None, max_length=100),
    madhab: MadhabhType | None = Form(default=None),
    photo: UploadFile | None = File(default=None),
    user: CurrentUser = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
    storage: StorageService = Depends(get_storage_service),
) -> UserProfileResponse:
    return await service.update_me(user, display_name, madhab, photo, storage)


@router.delete(
    "/me",
    status_code=status.HTTP_202_ACCEPTED,
    summary="PDPO Phase-1 soft-delete — data purged within 30 days",
)
async def delete_me(
    user: CurrentUser = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> JSONResponse:
    await service.delete_me(user)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "detail": "Account deletion initiated. Data will be purged within 30 days."
        },
    )


@router.get(
    "/me/export",
    response_class=StreamingResponse,
    summary="Data portability export — PDPO 2025 compliance",
    responses={
        200: {"description": "JSON file download", "content": {"application/json": {}}}
    },
)
async def export_me(
    user: CurrentUser = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> StreamingResponse:
    data = await service.export_me(user)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="masjidkoi-export-{user.user_id}.json"'
            ),
            "Content-Length": str(len(data)),
        },
    )


@router.get(
    "/me/favourites",
    response_model=list[FavouriteMasjidResponse],
    summary="List bookmarked masjids",
)
async def list_favourites(
    user: CurrentUser = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> list[FavouriteMasjidResponse]:
    return await service.list_favourites(user)


@router.post(
    "/me/favourites/{masjid_id}",
    status_code=status.HTTP_201_CREATED,
    summary="Bookmark a masjid as favourite",
)
async def add_favourite(
    masjid_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> JSONResponse:
    await service.add_favourite(user, masjid_id)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"detail": "Masjid added to favourites"},
    )


@router.delete(
    "/me/favourites/{masjid_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a masjid from favourites",
)
async def remove_favourite(
    masjid_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> None:
    await service.remove_favourite(user, masjid_id)
