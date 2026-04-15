# CLAUDE.md — MasjidKoi Backend

Guidance for Claude Code when working in this repository.

## Project Overview

MasjidKoi backend is a **FastAPI + Python 3.12** REST API backed by **PostgreSQL 16 + PostGIS**,
connected through **PgBouncer** (transaction-pool mode). Dependency management uses **uv**.
The API serves the Next.js web admin panel and (future) React Native mobile app.

**Stack:** FastAPI · SQLAlchemy 2 (async) · asyncpg · Alembic · GeoAlchemy2 · uv · Docker Compose

---

## Always-Apply Rules

### 1. No direct database calls in routes

Routes must never import `AsyncSession` and call it directly.
All database I/O belongs in a **repository**.

```python
# WRONG — direct query in route
@router.get("/masjids/{id}")
async def get_masjid(id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Masjid).where(Masjid.masjid_id == id))
    return result.scalar_one_or_none()

# RIGHT — delegate to repository
@router.get("/masjids/{id}")
async def get_masjid(id: UUID, service: MasjidService = Depends(get_masjid_service)):
    return await service.get_by_id(id)
```

---

### 2. Repository → Service → Route pattern

Every feature follows this strict three-layer structure:

```
app/
  models/          ← SQLAlchemy ORM models (define first, always)
  repositories/    ← raw DB queries only, no business logic
  services/        ← business logic, orchestrates repositories
  routers/         ← HTTP layer only: parse request, call service, return response
  schemas/         ← Pydantic request/response models
  dependencies/    ← FastAPI Depends() factories
```

**Repository** — only raw SQL/ORM, no HTTP concerns:
```python
class MasjidRepository(BaseRepository):
    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def get_by_id(self, masjid_id: UUID) -> Masjid | None:
        result = await self.db.execute(
            select(Masjid).where(Masjid.masjid_id == masjid_id)
        )
        return result.scalar_one_or_none()
```

**Service** — business logic, calls repositories, raises `HTTPException`:
```python
class MasjidService:
    def __init__(self, db: AsyncSession):
        self.repo = MasjidRepository(db)

    async def get_by_id_or_404(self, masjid_id: UUID) -> Masjid:
        masjid = await self.repo.get_by_id(masjid_id)
        if not masjid:
            raise HTTPException(status_code=404, detail="Masjid not found")
        return masjid
```

**Route** — HTTP only, no SQL, no business logic:
```python
@router.get("/{masjid_id}", response_model=MasjidResponse)
async def get_masjid(
    masjid_id: UUID,
    service: MasjidService = Depends(get_masjid_service),
):
    return await service.get_by_id_or_404(masjid_id)
```

**Dependency factory** in `app/dependencies/`:
```python
def get_masjid_service(db: AsyncSession = Depends(get_db)) -> MasjidService:
    return MasjidService(db)
```

---

### 3. Model first, then migration — always

Never write a query against a table that doesn't have an Alembic migration.

**Workflow:**
```bash
# 1. Define the SQLAlchemy model in app/models/
# 2. Import it in migrations/env.py target_metadata
# 3. Auto-generate the migration
uv run alembic revision --autogenerate -m "add_masjid_facilities"

# 4. Review the generated file in migrations/versions/
# 5. Apply
DATABASE_URL=postgresql://masjidkoi:masjidkoi@localhost:5432/masjidkoi \
  uv run alembic upgrade head
```

Rules:
- Never write raw DDL by hand
- Never use `alembic stamp` to skip a migration
- Always run migrations directly against PostgreSQL (not via PgBouncer) — prepared statement conflicts
- Import every new model in `app/db/base.py` so autogenerate detects it

---

### 4. Async session — always short-lived, never stored

The session from `get_db()` must **never** be stored on an object or used outside the
request lifecycle. It is scoped to a single HTTP request.

```python
# WRONG — storing session on instance
class MasjidService:
    session = db  # ← held forever, not released

# RIGHT — pass session into repository for the lifetime of one call
class MasjidService:
    def __init__(self, db: AsyncSession):
        self.repo = MasjidRepository(db)
```

The session is configured with:
- `NullPool` — SQLAlchemy holds zero connections; PgBouncer owns the pool
- `autoflush=False` — no implicit flushes that could start hidden transactions
- `expire_on_commit=False` — ORM objects are safe to read after commit
- `statement_cache_size=0` + `prepared_statement_cache_size=0` — required for PgBouncer
  transaction mode; without these asyncpg reuses prepared statement names across different
  server connections which PgBouncer cannot route

Always commit explicitly in the service layer after write operations:
```python
async def create_masjid(self, data: MasjidCreate) -> Masjid:
    masjid = await self.repo.create(data)
    await self.repo.commit()   # ← explicit
    return masjid
```

---

### 5. Async and concurrency

**Never block the event loop.** Uvicorn runs on a single async event loop; a blocking call
freezes all concurrent requests.

```python
# WRONG — blocking I/O on the event loop
import time
time.sleep(2)
result = requests.get(url)

# RIGHT — async equivalents
await asyncio.sleep(2)
async with httpx.AsyncClient() as client:
    result = await client.get(url)
```

When running multiple independent async operations, use `asyncio.gather()`:
```python
# WRONG — sequential awaits when work is independent
prayer_times = await prayer_repo.get_today(masjid_id)
facilities   = await facility_repo.get(masjid_id)

# RIGHT — concurrent
prayer_times, facilities = await asyncio.gather(
    prayer_repo.get_today(masjid_id),
    facility_repo.get(masjid_id),
)
```

Do NOT use `asyncio.gather()` across two operations that share the **same** session —
SQLAlchemy async sessions are not concurrency-safe. Use separate sessions or
sequence the calls.

CPU-bound work (PDF generation, image processing) must be offloaded:
```python
result = await asyncio.get_event_loop().run_in_executor(None, cpu_bound_fn, arg)
```

---

### 6. Schema validation at the boundary

All request bodies and response payloads must go through a **Pydantic schema**.
Never return a raw ORM model from a route.

```python
# WRONG
return masjid  # raw ORM object

# RIGHT
return MasjidResponse.model_validate(masjid)
# or use response_model= on the route decorator
```

Use separate schemas for create / update / response — never reuse the same model
for input and output.

---

### 7. Error handling conventions

- Raise `HTTPException` only in **services** or route handlers, never in repositories.
- Repositories raise `SQLAlchemyError` subclasses — let them propagate to `get_db()`
  which handles rollback.
- Never swallow exceptions silently; always log before re-raising.

```python
# WRONG
except Exception:
    pass

# RIGHT
except Exception:
    logger.exception("Failed to create masjid", extra={"data": data})
    raise
```

---

### 8. PostGIS spatial queries

Always use `GeoAlchemy2` functions, never raw string interpolation for coordinates.

```python
from geoalchemy2.functions import ST_DWithin, ST_MakePoint, ST_Distance

stmt = (
    select(Masjid, ST_Distance(Masjid.location, point).label("distance_m"))
    .where(ST_DWithin(Masjid.location, point, radius_m))
    .where(Masjid.status == "Active")
    .order_by("distance_m")
)
```

Spatial index on `masjids.location` (GIST) is created in migration `0001`. Always
filter `status = 'Active'` before the spatial predicate so the index is used.

---

## Development Commands

```bash
# Install dependencies
uv sync

# Run dev server (hot reload)
docker compose up

# Create a new migration after defining a model
uv run alembic revision --autogenerate -m "describe_change"

# Apply migrations (direct to postgres, not pgbouncer)
DATABASE_URL=postgresql://masjidkoi:masjidkoi@localhost:5432/masjidkoi \
  uv run alembic upgrade head

# Run linter
uv run ruff check .
uv run ruff format .

# Health check
curl http://localhost:8000/health
```

## File Naming

| Layer | Location | Example |
|---|---|---|
| Model | `app/models/masjid.py` | `class Masjid(Base)` |
| Repository | `app/repositories/masjid_repository.py` | `class MasjidRepository` |
| Service | `app/services/masjid_service.py` | `class MasjidService` |
| Router | `app/routers/masjids.py` | `router = APIRouter(prefix="/masjids")` |
| Schema | `app/schemas/masjid.py` | `class MasjidCreate`, `MasjidResponse` |
| Dependency | `app/dependencies/masjid.py` | `def get_masjid_service(...)` |
