"""Shared test fixtures — the repo's first backend test suite (PRD 07).

Tests run in-process against the live Postgres (the dev DB on localhost:5432,
migrations already applied). The app's own engine points at PgBouncer's
container hostname, so we build a host-reachable test engine and override
get_db with it. Each test seeds rows under random UUIDs and the `seed` fixture
cascades them away on teardown — no shared state between tests.
"""

import os
import time
import uuid
from datetime import date, datetime, timezone
from datetime import time as dtime

import jwt
import pytest_asyncio
from geoalchemy2.elements import WKTElement
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.announcement import Announcement
from app.models.device_token import DeviceToken
from app.models.masjid import Masjid
from app.models.masjid_event import EventRsvp, MasjidEvent
from app.models.masjid_review import MasjidReview
from app.models.user_badge import UserBadge
from app.models.user_checkin import UserCheckin
from app.models.user_journal_entry import UserJournalEntry
from app.models.user_masjid_follow import UserMasjidFollow
from app.models.user_profile import UserProfile

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://masjidkoi:masjidkoi@localhost:5432/masjidkoi",
)

test_engine = create_async_engine(
    TEST_DB_URL,
    poolclass=NullPool,
    connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
)
TestSession = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


def make_token(user_id: uuid.UUID) -> str:
    payload = {
        "sub": str(user_id),
        "aud": settings.GOTRUE_JWT_AUD,
        "email": f"{user_id}@test.local",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "app_metadata": {"role": "app_user"},
        "role": "authenticated",
        "aal": "aal1",
    }
    return jwt.encode(payload, settings.GOTRUE_JWT_SECRET, algorithm="HS256")


def auth_headers(user_id: uuid.UUID) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id)}"}


class Seeder:
    """Creates rows on a session and remembers them for cascade cleanup."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.masjid_ids: list[uuid.UUID] = []
        self.user_ids: list[uuid.UUID] = []

    async def masjid(self, name: str = "Test Masjid") -> Masjid:
        m = Masjid(
            name=name,
            address="123 Test Rd",
            admin_region="Dhaka",
            location=WKTElement("POINT(90.4 23.8)", srid=4326),
            status="active",
            verified=True,
        )
        self.db.add(m)
        await self.db.flush()
        self.masjid_ids.append(m.masjid_id)
        return m

    async def user(self, digest_hour: int = 19) -> uuid.UUID:
        uid = uuid.uuid4()
        self.db.add(
            UserProfile(user_id=uid, display_name="Tester", digest_hour=digest_hour)
        )
        await self.db.flush()
        self.user_ids.append(uid)
        return uid

    async def follow(
        self, user_id: uuid.UUID, masjid_id: uuid.UUID, mode: str = "digest"
    ) -> None:
        self.db.add(
            UserMasjidFollow(
                user_id=user_id, masjid_id=masjid_id, notification_mode=mode
            )
        )
        await self.db.flush()

    async def device(self, user_id: uuid.UUID) -> None:
        self.db.add(
            DeviceToken(
                token=f"tok-{uuid.uuid4()}", user_id=user_id, platform="android"
            )
        )
        await self.db.flush()

    async def announcement(
        self,
        masjid_id: uuid.UUID,
        *,
        title: str = "Notice",
        published: bool = True,
        published_at: datetime | None = None,
    ) -> Announcement:
        a = Announcement(
            masjid_id=masjid_id,
            title=title,
            body="Body text for the announcement.",
            is_published=published,
            published_at=published_at
            or (datetime.now(timezone.utc) if published else None),
            posted_by_id=uuid.uuid4(),
        )
        self.db.add(a)
        await self.db.flush()
        return a

    async def event(
        self, masjid_id: uuid.UUID, *, event_date: date, title: str = "Event"
    ) -> MasjidEvent:
        e = MasjidEvent(
            masjid_id=masjid_id,
            title=title,
            description="Event description.",
            event_date=event_date,
            event_time=dtime(18, 0),
            location="Main hall",
            created_by_id=uuid.uuid4(),
        )
        self.db.add(e)
        await self.db.flush()
        return e

    async def commit(self) -> None:
        await self.db.commit()

    async def cleanup(self) -> None:
        for uid in self.user_ids:
            await self.db.execute(delete(EventRsvp).where(EventRsvp.user_id == uid))
            await self.db.execute(
                delete(UserMasjidFollow).where(UserMasjidFollow.user_id == uid)
            )
            await self.db.execute(
                delete(MasjidReview).where(MasjidReview.user_id == uid)
            )
            await self.db.execute(delete(DeviceToken).where(DeviceToken.user_id == uid))
            await self.db.execute(
                delete(UserJournalEntry).where(UserJournalEntry.user_id == uid)
            )
            await self.db.execute(delete(UserBadge).where(UserBadge.user_id == uid))
            await self.db.execute(delete(UserCheckin).where(UserCheckin.user_id == uid))
            await self.db.execute(delete(UserProfile).where(UserProfile.user_id == uid))
        for mid in self.masjid_ids:
            # Masjid FKs cascade to announcements/events/follows/reviews.
            await self.db.execute(delete(Masjid).where(Masjid.masjid_id == mid))
        await self.db.commit()


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture
async def seed(db: AsyncSession) -> Seeder:
    s = Seeder(db)
    try:
        yield s
    finally:
        await s.cleanup()


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async def _override_get_db():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
