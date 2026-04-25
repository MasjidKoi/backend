import uuid
from typing import Any

from geoalchemy2.functions import ST_Distance, ST_DWithin
from sqlalchemy import and_, delete, func, or_, select, update
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
        *,
        has_parking: bool | None = None,
        has_sisters_section: bool | None = None,
        has_wheelchair_access: bool | None = None,
        has_wudu_area: bool | None = None,
        has_janazah: bool | None = None,
        has_school: bool | None = None,
    ) -> list[tuple[Masjid, float]]:
        """
        PostGIS ST_DWithin radius search with optional facility filters.
        CRITICAL: status filter applied BEFORE spatial predicate so the GIST
        index on location is used.  Longitude is the x-axis (first arg).
        JOIN with masjid_facilities is added only when facility filters are requested.
        """
        point = func.ST_GeographyFromText(f"SRID=4326;POINT({lng} {lat})")
        distance_expr = ST_Distance(Masjid.location, point).label("distance_m")

        stmt = (
            select(Masjid, distance_expr)
            .where(Masjid.status == MasjidStatus.ACTIVE)
            .where(ST_DWithin(Masjid.location, point, radius_m))
            .order_by(distance_expr)
            .limit(limit)
        )

        # Apply facility filters — JOIN only when at least one filter is provided
        facility_filters: dict[str, bool] = {
            k: v
            for k, v in {
                "has_parking": has_parking,
                "has_sisters_section": has_sisters_section,
                "has_wheelchair_access": has_wheelchair_access,
                "has_wudu_area": has_wudu_area,
                "has_janazah": has_janazah,
                "has_school": has_school,
            }.items()
            if v is not None
        }
        if facility_filters:
            stmt = stmt.join(
                MasjidFacilities,
                Masjid.masjid_id == MasjidFacilities.masjid_id,
            )
            for col, val in facility_filters.items():
                stmt = stmt.where(getattr(MasjidFacilities, col) == val)

        rows = (await self.db.execute(stmt)).all()
        return [(row[0], float(row[1])) for row in rows]

    async def get_stats(self) -> dict:
        """Aggregated counts for the admin dashboard."""
        result = await self.db.execute(
            select(
                func.count().label("total"),
                func.count()
                .filter(Masjid.status == MasjidStatus.ACTIVE)
                .label("active"),
                func.count()
                .filter(Masjid.status == MasjidStatus.PENDING)
                .label("pending"),
                func.count()
                .filter(Masjid.status == MasjidStatus.SUSPENDED)
                .label("suspended"),
                func.count().filter(Masjid.verified == True).label("verified"),  # noqa: E712
            ).select_from(Masjid)
        )
        row = result.one()
        return {
            "total_masjids": row.total,
            "active_masjids": row.active,
            "pending_masjids": row.pending,
            "suspended_masjids": row.suspended,
            "verified_masjids": row.verified,
        }

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
            (await self.db.execute(data_stmt.offset(offset).limit(limit)))
            .scalars()
            .all()
        )
        return rows, total

    # ── Writes ─────────────────────────────────────────────────────────────────

    async def list_all_for_export(
        self,
        *,
        status: str | None = None,
        admin_region: str | None = None,
        verified: bool | None = None,
    ) -> list[Masjid]:
        """Unpaginated SELECT for export — all masjids matching filters."""
        filters: list[Any] = []
        if status:
            filters.append(Masjid.status == status)
        if admin_region:
            filters.append(Masjid.admin_region.ilike(f"%{admin_region}%"))
        if verified is not None:
            filters.append(Masjid.verified == verified)

        stmt = (
            select(Masjid)
            .options(
                selectinload(Masjid.facilities),
                selectinload(Masjid.contact),
            )
            .order_by(Masjid.created_at.desc())
        )
        if filters:
            stmt = stmt.where(and_(*filters))
        return list((await self.db.execute(stmt)).scalars().all())

    async def reassign_related_records(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
    ) -> None:
        """Bulk-UPDATE all 1:many children from source → target masjid."""
        from app.models.announcement import Announcement
        from app.models.masjid import MasjidPhoto
        from app.models.masjid_report import MasjidReport
        from app.models.prayer_times import PrayerTimeRecord
        from app.models.user_masjid_follow import UserMasjidFollow

        for Model, col in [
            (MasjidPhoto, MasjidPhoto.masjid_id),
            (PrayerTimeRecord, PrayerTimeRecord.masjid_id),
            (Announcement, Announcement.masjid_id),
        ]:
            await self.db.execute(
                update(Model).where(col == source_id).values(masjid_id=target_id)
            )

        await self.db.execute(
            update(MasjidReport)
            .where(MasjidReport.masjid_id == source_id)
            .where(MasjidReport.masjid_id.is_not(None))
            .values(masjid_id=target_id)
        )

        # Transfer followers — skip rows that would violate the unique constraint
        # (user already follows the target); delete those from source first.
        from sqlalchemy import delete as sa_delete

        target_followers_subq = (
            select(UserMasjidFollow.user_id)
            .where(UserMasjidFollow.masjid_id == target_id)
            .scalar_subquery()
        )
        await self.db.execute(
            sa_delete(UserMasjidFollow)
            .where(UserMasjidFollow.masjid_id == source_id)
            .where(UserMasjidFollow.user_id.in_(target_followers_subq))
        )
        await self.db.execute(
            update(UserMasjidFollow)
            .where(UserMasjidFollow.masjid_id == source_id)
            .values(masjid_id=target_id)
        )
        await self.db.flush()

    async def delete_conflicting_prayer_times(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
    ) -> int:
        """Delete source prayer_time rows for dates already on target (avoids unique constraint violation)."""
        from app.models.prayer_times import PrayerTimeRecord as PTR

        target_dates_subq = (
            select(PTR.date).where(PTR.masjid_id == target_id).scalar_subquery()
        )
        result = await self.db.execute(
            delete(PTR)
            .where(PTR.masjid_id == source_id)
            .where(PTR.date.in_(target_dates_subq))
        )
        await self.db.flush()
        return result.rowcount

    async def get_1to1_children(
        self, masjid_id: uuid.UUID
    ) -> "tuple[MasjidFacilities | None, MasjidContact | None, Any]":
        """Load the three 1:1 child rows sequentially (same session — no gather)."""
        from app.models.prayer_times import JumahSchedule

        fac = (
            await self.db.execute(
                select(MasjidFacilities).where(MasjidFacilities.masjid_id == masjid_id)
            )
        ).scalar_one_or_none()

        con = (
            await self.db.execute(
                select(MasjidContact).where(MasjidContact.masjid_id == masjid_id)
            )
        ).scalar_one_or_none()

        jum = (
            await self.db.execute(
                select(JumahSchedule).where(JumahSchedule.masjid_id == masjid_id)
            )
        ).scalar_one_or_none()

        return fac, con, jum

    async def update_jumah(self, masjid_id: uuid.UUID, fields: dict) -> Any:
        """Mirrors update_facilities / update_contact pattern for JumahSchedule."""
        from app.models.prayer_times import JumahSchedule

        result = await self.db.execute(
            select(JumahSchedule).where(JumahSchedule.masjid_id == masjid_id)
        )
        jumah = result.scalar_one()
        for key, value in fields.items():
            setattr(jumah, key, value)
        await self.db.flush()
        return jumah

    async def create(
        self,
        name: str,
        address: str,
        admin_region: str,
        lat: float,
        lng: float,
        timezone: str = "Asia/Dhaka",
        description: str | None = None,
        donations_enabled: bool = False,
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
            donations_enabled=donations_enabled,
        )
        self.db.add(masjid)
        await self.db.flush()

        # Auto-create 1:1 child rows so every masjid always has them
        from app.models.prayer_times import JumahSchedule

        self.db.add(MasjidFacilities(masjid_id=masjid.masjid_id))
        self.db.add(MasjidContact(masjid_id=masjid.masjid_id))
        self.db.add(JumahSchedule(masjid_id=masjid.masjid_id))
        await self.db.flush()
        return masjid

    async def update_fields(self, masjid: Masjid, fields: dict) -> Masjid:
        """Partial update — only the keys present in `fields` are touched."""
        lat = fields.pop("latitude", None)
        lng = fields.pop("longitude", None)

        for key, value in fields.items():
            setattr(masjid, key, value)

        if lat is not None and lng is not None:
            masjid.location = func.ST_GeographyFromText(f"SRID=4326;POINT({lng} {lat})")
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

    async def update_contact(self, masjid_id: uuid.UUID, fields: dict) -> MasjidContact:
        result = await self.db.execute(
            select(MasjidContact).where(MasjidContact.masjid_id == masjid_id)
        )
        contact = result.scalar_one()
        for key, value in fields.items():
            setattr(contact, key, value)
        await self.db.flush()
        return contact
