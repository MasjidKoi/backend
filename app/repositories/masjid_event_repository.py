import uuid
from datetime import date

from sqlalchemy import func, select

from app.models.masjid_event import EventRsvp, MasjidEvent
from app.repositories.base import BaseRepository


class MasjidEventRepository(BaseRepository[MasjidEvent]):
    model = MasjidEvent

    # ── Events ────────────────────────────────────────────────────────────────

    async def list_upcoming(
        self, masjid_id: uuid.UUID, offset: int, limit: int
    ) -> tuple[list, int]:
        rsvp_sq = (
            select(func.count())
            .where(EventRsvp.event_id == MasjidEvent.event_id)
            .correlate(MasjidEvent)
            .scalar_subquery()
        )
        base_filter = (
            MasjidEvent.masjid_id == masjid_id,
            MasjidEvent.event_date >= date.today(),
        )
        count_result = await self.db.execute(select(func.count()).where(*base_filter))
        total = count_result.scalar_one()
        rows = (
            await self.db.execute(
                select(MasjidEvent, rsvp_sq.label("rsvp_count"))
                .where(*base_filter)
                .order_by(MasjidEvent.event_date.asc(), MasjidEvent.event_time.asc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return rows, total

    async def get_by_id_and_masjid(
        self, event_id: uuid.UUID, masjid_id: uuid.UUID
    ) -> MasjidEvent | None:
        result = await self.db.execute(
            select(MasjidEvent)
            .where(MasjidEvent.event_id == event_id)
            .where(MasjidEvent.masjid_id == masjid_id)
        )
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> MasjidEvent:
        event = MasjidEvent(**kwargs)
        return await self.add(event)

    async def update(self, event: MasjidEvent, fields: dict) -> MasjidEvent:
        for k, v in fields.items():
            setattr(event, k, v)
        await self.db.flush()
        return event

    async def delete(self, event: MasjidEvent) -> None:
        await self.db.delete(event)
        await self.db.flush()

    async def get_rsvp_count(self, event_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).where(EventRsvp.event_id == event_id)
        )
        return result.scalar_one()

    # ── RSVPs ─────────────────────────────────────────────────────────────────

    async def get_rsvp(
        self, event_id: uuid.UUID, user_id: uuid.UUID
    ) -> EventRsvp | None:
        result = await self.db.execute(
            select(EventRsvp)
            .where(EventRsvp.event_id == event_id)
            .where(EventRsvp.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def create_rsvp(self, event_id: uuid.UUID, user_id: uuid.UUID) -> EventRsvp:
        rsvp = EventRsvp(event_id=event_id, user_id=user_id)
        self.db.add(rsvp)
        await self.db.flush()
        return rsvp

    async def delete_rsvp(self, rsvp: EventRsvp) -> None:
        await self.db.delete(rsvp)
        await self.db.flush()

    async def list_rsvps(
        self, event_id: uuid.UUID, masjid_id: uuid.UUID, offset: int, limit: int
    ) -> tuple[list[EventRsvp], int]:
        count_q = (
            select(func.count())
            .select_from(EventRsvp)
            .join(MasjidEvent, EventRsvp.event_id == MasjidEvent.event_id)
            .where(EventRsvp.event_id == event_id, MasjidEvent.masjid_id == masjid_id)
        )
        rows_q = (
            select(EventRsvp)
            .join(MasjidEvent, EventRsvp.event_id == MasjidEvent.event_id)
            .where(EventRsvp.event_id == event_id, MasjidEvent.masjid_id == masjid_id)
            .order_by(EventRsvp.rsvp_at.asc())
            .offset(offset)
            .limit(limit)
        )
        total = (await self.db.execute(count_q)).scalar_one()
        rows = list((await self.db.execute(rows_q)).scalars().all())
        return rows, total
