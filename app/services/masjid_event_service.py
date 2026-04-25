import uuid
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.repositories.masjid_event_repository import MasjidEventRepository
from app.repositories.masjid_repository import MasjidRepository
from app.schemas.masjid_event import (
    EventAttendeeListResponse,
    EventAttendeeResponse,
    EventCreate,
    EventListResponse,
    EventResponse,
    EventUpdate,
)


class MasjidEventService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = MasjidEventRepository(db)
        self.masjid_repo = MasjidRepository(db)

    def _check_scope(self, user: CurrentUser, masjid_id: uuid.UUID) -> None:
        if user.is_platform_admin:
            return
        if user.masjid_id != masjid_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to your own masjid",
            )

    def _to_response(self, event, rsvp_count: int) -> EventResponse:
        return EventResponse(
            event_id=event.event_id,
            masjid_id=event.masjid_id,
            title=event.title,
            description=event.description,
            event_date=event.event_date,
            event_time=event.event_time,
            location=event.location,
            capacity=event.capacity,
            rsvp_enabled=event.rsvp_enabled,
            rsvp_count=rsvp_count,
            created_by_email=event.created_by_email,
            created_at=event.created_at,
            updated_at=event.updated_at,
        )

    async def _get_masjid_or_404(self, masjid_id: uuid.UUID):
        m = await self.masjid_repo.get_by_id(masjid_id)
        if not m:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Masjid not found"
            )
        return m

    async def _get_event_or_404(self, event_id: uuid.UUID, masjid_id: uuid.UUID):
        e = await self.repo.get_by_id_and_masjid(event_id, masjid_id)
        if not e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Event not found"
            )
        return e

    async def list_upcoming(
        self, masjid_id: uuid.UUID, page: int, page_size: int
    ) -> EventListResponse:
        await self._get_masjid_or_404(masjid_id)
        rows, total = await self.repo.list_upcoming(
            masjid_id, offset=(page - 1) * page_size, limit=page_size
        )
        return EventListResponse(
            items=[self._to_response(ev, cnt) for ev, cnt in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def create(
        self, masjid_id: uuid.UUID, data: EventCreate, user: CurrentUser
    ) -> EventResponse:
        self._check_scope(user, masjid_id)
        await self._get_masjid_or_404(masjid_id)
        if data.event_date < date.today():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="event_date must be today or in the future",
            )
        event = await self.repo.create(
            event_id=uuid.uuid4(),
            masjid_id=masjid_id,
            title=data.title,
            description=data.description,
            event_date=data.event_date,
            event_time=data.event_time,
            location=data.location,
            capacity=data.capacity,
            rsvp_enabled=data.rsvp_enabled,
            created_by_id=user.user_id,
            created_by_email=user.email,
        )
        await self.repo.commit()
        return self._to_response(event, 0)

    async def update(
        self,
        masjid_id: uuid.UUID,
        event_id: uuid.UUID,
        data: EventUpdate,
        user: CurrentUser,
    ) -> EventResponse:
        self._check_scope(user, masjid_id)
        event = await self._get_event_or_404(event_id, masjid_id)
        fields = data.model_dump(exclude_unset=True)
        if "event_date" in fields and fields["event_date"] < date.today():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="event_date must be today or in the future",
            )
        await self.repo.update(event, fields)
        await self.repo.commit()
        # Reload after commit — onupdate=func.now() expires updated_at on flush
        event = await self._get_event_or_404(event_id, masjid_id)
        rsvp_count = await self.repo.get_rsvp_count(event_id)
        return self._to_response(event, rsvp_count)

    async def delete(
        self, masjid_id: uuid.UUID, event_id: uuid.UUID, user: CurrentUser
    ) -> None:
        self._check_scope(user, masjid_id)
        event = await self._get_event_or_404(event_id, masjid_id)
        await self.repo.delete(event)
        await self.repo.commit()

    async def toggle_rsvp(
        self, masjid_id: uuid.UUID, event_id: uuid.UUID, user: CurrentUser
    ) -> dict:
        event = await self._get_event_or_404(event_id, masjid_id)
        if not event.rsvp_enabled:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="RSVP is not enabled for this event",
            )
        existing = await self.repo.get_rsvp(event_id, user.user_id)
        if existing:
            await self.repo.delete_rsvp(existing)
            await self.repo.commit()
            count = await self.repo.get_rsvp_count(event_id)
            return {"rsvp": False, "rsvp_count": count}
        if event.capacity is not None:
            count = await self.repo.get_rsvp_count(event_id)
            if count >= event.capacity:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Event is at full capacity",
                )
        await self.repo.create_rsvp(event_id, user.user_id)
        await self.repo.commit()
        count = await self.repo.get_rsvp_count(event_id)
        return {"rsvp": True, "rsvp_count": count}

    async def list_attendees(
        self,
        masjid_id: uuid.UUID,
        event_id: uuid.UUID,
        page: int,
        page_size: int,
        user: CurrentUser,
    ) -> EventAttendeeListResponse:
        self._check_scope(user, masjid_id)
        rows, total = await self.repo.list_rsvps(
            event_id, masjid_id, offset=(page - 1) * page_size, limit=page_size
        )
        return EventAttendeeListResponse(
            items=[
                EventAttendeeResponse(user_id=r.user_id, rsvp_at=r.rsvp_at)
                for r in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )
