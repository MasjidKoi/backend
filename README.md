# MasjidKoi Backend

FastAPI REST API for the MasjidKoi masjid discovery and management platform.

## Stack

- **Python 3.12** — runtime
- **FastAPI** — async web framework
- **SQLAlchemy 2 + asyncpg** — async ORM
- **PostgreSQL 16 + PostGIS** — spatial database
- **PgBouncer** — connection pooling (transaction mode)
- **GoTrue** — JWT auth (Supabase fork)
- **Alembic** — database migrations
- **uv** — package manager

## Quick Start

### Prerequisites

- Docker & Docker Compose
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

### 1. Environment

```bash
cp .env.example .env
# Edit .env with your secrets
```

### 2. Start services

```bash
docker compose up
```

API available at `http://localhost:8001` — health check: `GET /health`

### 3. Apply migrations

```bash
# Run inside the api container (postgres is not exposed to the host)
docker compose exec api uv run alembic upgrade head
```

### 4. Seed platform admin

```bash
uv run python scripts/seed_platform_admin.py
```

## Development

```bash
uv sync                          # install dependencies
uv run ruff check .              # lint
uv run ruff format .             # format
```

## Project Structure

```
app/
  models/        SQLAlchemy ORM models
  repositories/  raw DB queries (no business logic)
  services/      business logic, raises HTTPException
  routers/       HTTP layer — parse request, call service, return response
  schemas/       Pydantic request/response schemas
  dependencies/  FastAPI Depends() factories
  core/          config, security (JWT decode)
  db/            session factory
migrations/      Alembic migrations
scripts/         one-off admin utilities
```

## API Docs

Swagger UI: `http://localhost:8001/docs`
