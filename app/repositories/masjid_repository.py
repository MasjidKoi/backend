import uuid
from typing import Any

from geoalchemy2.functions import ST_Distance, ST_DWithin
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from app.models.enums import MasjidStatus
from app.models.masjid import Masjid, MasjidContact, MasjidFacilities
from app.repositories.base import BaseRepository


class MasjidRepository(BaseRepository[Masjid]):
    model = Masjid

    # ── Reads ──────────────────────────────────────────────────────────────────

    async def get_by_id_with_relations(self, masjid_id: uuid.UUID) -> Masjid | None:
        """Full profile: masjid + facilities + contact + photos via selectinload."""
        from app.models.masjid import MasjidPhoto

        result = await self.db.execute(
            select(Masjid)
            .options(
                selectinload(Masjid.facilities),
                selectinload(Masjid.contact),
                selectinload(Masjid.photos),
            )
            .where(Masjid.masjid_id == masjid_id)
        )
        return result.scalar_one_or_none()

    async def get_nearby(
        self,
        lat: float,
        lng: float,
        radius_m: float,
        limit: int = 50,
    ) -> list[tuple[Masjid, float]]:
        """
        PostGIS ST_DWithin radius search.
        CRITICAL: status filter applied BEFORE spatial predicate so the GIST
        index on location is used.  Longitude is the x-axis (first arg).
        """
        # Use ST_GeographyFromText with validated floats — avoids .cast("geography")
        # which passes a raw string to SQLAlchemy's type system and breaks cache key.
        point = func.ST_GeographyFromText(f"SRID=4326;POINT({lng} {lat})")
        distance_expr = ST_Distance(Masjid.location, point).label("distance_m")

        stmt = (
            select(Masjid, distance_expr)
            .where(Masjid.status == MasjidStatus.ACTIVE)
            .where(ST_DWithin(Masjid.location, point, radius_m))
            .order_by(distance_expr)
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        return [(row[0], float(row[1])) for row in rows]

    async def search(self, q: str, limit: int = 20) -> list[Masjid]:
        """Case-insensitive name/region autocomplete — active masjids only."""
        pattern = f"%{q}%"
        stmt = (
            select(Masjid)
            .where(Masjid.status == MasjidStatus.ACTIVE)
            .where(
                or_(
                    Masjid.name.ilike(pattern),
                    Masjid.admin_region.ilike(pattern),
                )
            )
            .order_by(Masjid.name)
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_for_admin(
        self,
        *,
        status: str | None = None,
        admin_region: str | None = None,
        verified: bool | None = None,
        q: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[Masjid], int]:
        filters: list[Any] = []
        if status:
            filters.append(Masjid.status == status)
        if admin_region:
            filters.append(Masjid.admin_region.ilike(f"%{admin_region}%"))
        if verified is not None:
            filters.append(Masjid.verified == verified)
        if q:
            filters.append(
                or_(
                    Masjid.name.ilike(f"%{q}%"),
                    Masjid.address.ilike(f"%{q}%"),
                )
            )

        where = and_(*filters) if filters else None

        count_stmt = select(func.count()).select_from(Masjid)
        data_stmt = select(Masjid).order_by(Masjid.created_at.desc())
        if where is not None:
            count_stmt = count_stmt.where(where)
            data_stmt = data_stmt.where(where)

        total: int = (await self.db.execute(count_stmt)).scalar_one()
        rows = list(
            (await self.db.execute(data_stmt.offset(offset).limit(limit))).scalars().all()
        )
        return rows, total

    # ── Writes ─────────────────────────────────────────────────────────────────

    async def create(
        self,
        name: str,
        address: str,
        admin_region: str,
        lat: float,
        lng: float,
        timezone: str = "Asia/Dhaka",
        description: str | None = None,
    ) -> Masjid:
        """
        Insert masjid + auto-create facilities + contact rows in one flush.
        Location uses ST_GeographyFromText with validated float values.
        """
        location = func.ST_GeographyFromText(f"SRID=4326;POINT({lng} {lat})")
        masjid = Masjid(
            name=name,
            address=address,
            admin_region=admin_region,
            location=location,
            timezone=timezone,
            description=description,
        )
        self.db.add(masjid)
        await self.db.flush()

        # Auto-create 1:1 child rows so every masjid always has them
        self.db.add(MasjidFacilities(masjid_id=masjid.masjid_id))
        self.db.add(MasjidContact(masjid_id=masjid.masjid_id))
        await self.db.flush()
        return masjid

    async def update_fields(self, masjid: Masjid, fields: dict) -> Masjid:
        """Partial update — only the keys present in `fields` are touched."""
        lat = fields.pop("latitude", None)
        lng = fields.pop("longitude", None)

        for key, value in fields.items():
            setattr(masjid, key, value)

        if lat is not None and lng is not None:
            masjid.location = func.ST_GeographyFromText(
                f"SRID=4326;POINT({lng} {lat})"
            )
        await self.db.flush()
        return masjid

    async def set_verified(self, masjid: Masjid, verified: bool) -> Masjid:
        masjid.verified = verified
        await self.db.flush()
        return masjid

    async def set_status(
        self, masjid: Masjid, status: MasjidStatus, reason: str | None = None
    ) -> Masjid:
        masjid.status = status
        if reason is not None:
            masjid.suspension_reason = reason
        await self.db.flush()
        return masjid

    async def update_facilities(
        self, masjid_id: uuid.UUID, fields: dict
    ) -> MasjidFacilities:
        result = await self.db.execute(
            select(MasjidFacilities).where(MasjidFacilities.masjid_id == masjid_id)
        )
        facilities = result.scalar_one()
        for key, value in fields.items():
            setattr(facilities, key, value)
        await self.db.flush()
        return facilities

    async def update_contact(
        self, masjid_id: uuid.UUID, fields: dict
    ) -> MasjidContact:
        result = await self.db.execute(
            select(MasjidContact).where(MasjidContact.masjid_id == masjid_id)
        )
        contact = result.scalar_one()
        for key, value in fields.items():
            setattr(contact, key, value)
        await self.db.flush()
        return contact
