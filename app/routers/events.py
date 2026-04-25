import uuid

from fastapi import APIRouter, Depends, Query, status

from app.core.security import CurrentUser
from app.dependencies.auth import get_current_user, require_masjid_admin
from app.dependencies.masjid_event import get_event_service
from app.schemas.masjid_event import (
    EventAttendeeListResponse,
    EventCreate,
    EventListResponse,
    EventResponse,
    EventUpdate,
)
from app.services.masjid_event_service import MasjidEventService

router = APIRouter(prefix="/masjids", tags=["events"])


@router.get(
    "/{masjid_id}/events",
    response_model=EventListResponse,
    summary="List upcoming events for a masjid — public",
)
async def list_events(
    masjid_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: MasjidEventService = Depends(get_event_service),
) -> EventListResponse:
    return await service.list_upcoming(masjid_id, page, page_size)


@router.post(
    "/{masjid_id}/events",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an event for a masjid (masjid_admin)",
)
async def create_event(
    masjid_id: uuid.UUID,
    body: EventCreate,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidEventService = Depends(get_event_service),
) -> EventResponse:
    return await service.create(masjid_id, body, user)


@router.patch(
    "/{masjid_id}/events/{event_id}",
    response_model=EventResponse,
    summary="Update an event (masjid_admin)",
)
async def update_event(
    masjid_id: uuid.UUID,
    event_id: uuid.UUID,
    body: EventUpdate,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidEventService = Depends(get_event_service),
) -> EventResponse:
    return await service.update(masjid_id, event_id, body, user)


@router.delete(
    "/{masjid_id}/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an event (masjid_admin)",
)
async def delete_event(
    masjid_id: uuid.UUID,
    event_id: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidEventService = Depends(get_event_service),
) -> None:
    await service.delete(masjid_id, event_id, user)


@router.post(
    "/{masjid_id}/events/{event_id}/rsvp",
    summary="Toggle RSVP for an event (authenticated user)",
)
async def toggle_rsvp(
    masjid_id: uuid.UUID,
    event_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    service: MasjidEventService = Depends(get_event_service),
) -> dict:
    return await service.toggle_rsvp(masjid_id, event_id, user)


@router.get(
    "/{masjid_id}/events/{event_id}/attendees",
    response_model=EventAttendeeListResponse,
    summary="List RSVP attendees for an event (masjid_admin)",
)
async def list_attendees(
    masjid_id: uuid.UUID,
    event_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(require_masjid_admin),
    service: MasjidEventService = Depends(get_event_service),
) -> EventAttendeeListResponse:
    return await service.list_attendees(masjid_id, event_id, page, page_size, user)
