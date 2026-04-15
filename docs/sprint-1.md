# Sprint 1 — Backend Implementation Plan

**Project:** MasjidKoi  
**Team:** T40 — Insanity Check  
**Sprint Duration:** Months 1–3 (Phase 1 MVP)  
**Stack:** FastAPI · Python 3.12 · PostgreSQL 16 + PostGIS · PgBouncer · SQLAlchemy 2 (async) · Alembic · uv · Docker Compose

---

## Architecture Overview

```
Client (Next.js Admin / Mobile)
        │  HTTPS
        ▼
  FastAPI (Uvicorn)
        │
        ▼
  PgBouncer :6432  ← transaction-mode pool, 20 server conns, 1000 client conns
        │
        ▼
  PostgreSQL 16 + PostGIS :5432
```

SQLAlchemy uses `NullPool` so it never holds a connection open — PgBouncer owns all pooling. Alembic migrations connect **directly** to PostgreSQL (bypassing PgBouncer) to avoid prepared-statement conflicts.

---

## Module Breakdown

### 1. Authentication (`app/routers/auth.py`)

**SRS refs:** FR-T40-072, FR-T40-073, FR-T40-075, FR-T40-076

Admin authentication only (no mobile OTP in this sprint — GoTrue handles that separately).

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/auth/admin/login` | POST | None | Email + password; returns pre-auth token |
| `/auth/admin/2fa/verify` | POST | Pre-auth token | TOTP code verification; issues final JWT |
| `/auth/admin/refresh` | POST | Refresh token | Issue new access token |
| `/auth/admin/logout` | POST | JWT | Invalidate session |
| `/auth/password/reset-request` | POST | None | Send reset email |
| `/auth/password/reset` | POST | Reset token | Update password |

**Implementation details:**
- JWT signed with HS256, 30-minute access token TTL, 7-day refresh token
- TOTP via `pyotp`; QR code provisioning for Super Admin accounts
- Passwords hashed with `bcrypt` (cost factor 12)
- Refresh tokens stored in `admin_sessions` table (not in-memory) for revocation support
- All auth routes exempt from `LoggingMiddleware` request body logging (no credential leaks)
- Rate limiting: 5 attempts/minute per IP on login endpoint

**Models:**
```
admin_users (user_id UUID PK, email, password_hash, role ENUM[super_admin, masjid_admin],
             totp_secret, is_active, created_at, updated_at)

admin_sessions (session_id UUID PK, user_id FK, refresh_token_hash,
                device_info, created_at, expires_at, revoked_at)
```

---

### 2. Masjid Service (`app/routers/masjids.py`)

**SRS refs:** FR-T40-008, FR-T40-009, FR-T40-010, FR-T40-011, FR-T40-017, FR-T40-079–087, FR-T40-088–095

Core resource of the platform. Every other feature hangs off a `masjid_id`.

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/masjids` | POST | Super Admin | Create masjid account |
| `/masjids` | GET | None | List all masjids (paginated, filterable) |
| `/masjids/nearby` | GET | Optional | PostGIS radius search (`?lat&lng&radius_m`) |
| `/masjids/search` | GET | None | Name/area autocomplete (`?q=`) |
| `/masjids/{id}` | GET | None | Full masjid profile with facilities |
| `/masjids/{id}` | PATCH | Masjid Admin | Update own masjid profile |
| `/masjids/{id}/verify` | POST | Super Admin | Grant verified badge |
| `/masjids/{id}/suspend` | POST | Super Admin | Suspend with reason |
| `/masjids/{id}/photos` | POST | Masjid Admin | Upload up to 10 photos |
| `/masjids/{id}/photos/{photo_id}` | DELETE | Masjid Admin | Remove a photo |

**Spatial query (PostGIS):**
```sql
SELECT *, ST_Distance(location, ST_MakePoint(:lng, :lat)::geography) AS distance_m
FROM masjids
WHERE ST_DWithin(location, ST_MakePoint(:lng, :lat)::geography, :radius_m)
  AND status = 'Active'
ORDER BY distance_m
LIMIT 50;
```
GIST spatial index on `masjids.location` targets ≤100 ms response even at scale.

**Models:**
```
masjids (masjid_id UUID PK, name VARCHAR(200), address TEXT, admin_region VARCHAR(100),
         location GEOGRAPHY(POINT,4326), status ENUM[Pending,Active,Suspended,Removed],
         verified BOOLEAN, donations_enabled BOOLEAN, timezone VARCHAR(50),
         created_at, updated_at)

masjid_facilities (masjid_id FK PK, has_sisters_section BOOL, has_wudu_area BOOL,
                   has_wheelchair_access BOOL, has_parking BOOL, has_janazah BOOL,
                   has_school BOOL, parking_capacity INT, created_at, updated_at)

masjid_photos (photo_id UUID PK, masjid_id FK, url TEXT, is_cover BOOL,
               display_order INT, created_at)

masjid_contact (masjid_id FK PK, phone VARCHAR(20), email VARCHAR(255),
                whatsapp VARCHAR(20), website_url TEXT, updated_at)
```

---

### 3. Prayer Time Service (`app/routers/prayer_times.py`)

**SRS refs:** FR-T40-018–020, FR-T40-096–103

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/masjids/{id}/prayer-times` | GET | None | Get today's Azan + Iqamah times |
| `/masjids/{id}/prayer-times` | PUT | Masjid Admin | Set manual override for a date range |
| `/masjids/{id}/prayer-times/auto` | POST | Masjid Admin | Trigger recalculation via adhan library |
| `/masjids/{id}/jumah` | GET | None | Fetch Jumu'ah schedule |
| `/masjids/{id}/jumah` | PUT | Masjid Admin | Update Jumu'ah times + khutbah language |

**Calculation engine:**
- Uses the Python `adhan` library for astronomical calculation
- Supports all 4 madhabs: Hanafi, Shafi'i, Maliki, Hanbali
- Auto-calculates on first request for a given date; result cached in `prayer_times` table
- Admin can override any specific date; override is flagged `is_manual = True`
- Reverts to auto-calculation after the override date passes

**Models:**
```
prayer_times (pt_id UUID PK, masjid_id FK, date DATE,
              fajr_azan TIME, fajr_iqamah TIME,
              dhuhr_azan TIME, dhuhr_iqamah TIME,
              asr_azan TIME, asr_iqamah TIME,
              maghrib_azan TIME, maghrib_iqamah TIME,
              isha_azan TIME, isha_iqamah TIME,
              is_manual BOOL, madhab VARCHAR(20),
              created_at, updated_at,
              UNIQUE(masjid_id, date))

jumah_schedule (masjid_id FK PK, first_jumah TIME, second_jumah TIME,
                khutbah_language VARCHAR(50), updated_at)
```

---

### 4. Admin Panel Support (`app/routers/admin.py`)

**SRS refs:** FR-T40-072–078, FR-T40-079–087, FR-T40-121, FR-T40-129–133

Endpoints consumed exclusively by the Next.js admin panel.

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/admin/masjids` | GET | Super Admin | Searchable/filterable masjid table |
| `/admin/masjids/{id}/credentials` | POST | Super Admin | Generate and email masjid admin credentials |
| `/admin/users` | GET | Super Admin | List all app users |
| `/admin/users/{id}/suspend` | POST | Super Admin | Block user with reason |
| `/admin/users/{id}` | DELETE | Super Admin | Hard-delete (PDPO compliance) |
| `/admin/audit-log` | GET | Super Admin | Paginated audit log |
| `/admin/dashboard/stats` | GET | Super Admin | Live counters: masjids, users, donations |

**Audit log middleware:**
All `POST`, `PUT`, `PATCH`, `DELETE` requests on admin routes are intercepted by `AuditLogMiddleware`, which appends an immutable record to `audit_logs` before the response is sent.

**Models:**
```
audit_logs (log_id UUID PK, admin_id FK, action VARCHAR(50),
            target_entity VARCHAR(50), target_id UUID,
            payload JSONB, ip_address INET, created_at TIMESTAMPTZ NOT NULL)
```
Append-only: no UPDATE or DELETE permitted at the ORM layer.

---

### 5. Database & Migrations

**File:** `migrations/versions/`

Migrations are run **directly against PostgreSQL** (not through PgBouncer) to avoid prepared-statement conflicts with Alembic's DDL operations.

Migration order:
1. `0001_enable_postgis.py` — `CREATE EXTENSION IF NOT EXISTS postgis`
2. `0002_create_admin_users.py`
3. `0003_create_masjids.py` — includes `GEOGRAPHY(POINT,4326)` column + GIST index
4. `0004_create_masjid_facilities.py`
5. `0005_create_masjid_photos_and_contact.py`
6. `0006_create_prayer_times.py`
7. `0007_create_audit_logs.py`
8. `0008_create_admin_sessions.py`

Run via:
```bash
# Direct to postgres (bypasses pgbouncer)
DATABASE_URL=postgresql://masjidkoi:masjidkoi@localhost:5432/masjidkoi alembic upgrade head
```

---

### 6. Infrastructure

**File:** `docker-compose.yml`

| Container | Image | Port | Purpose |
|---|---|---|---|
| `masjidkoi_api` | local build | 8000 | FastAPI + Uvicorn |
| `masjidkoi_postgres` | postgis/postgis:16-3.5-alpine | 5432 | Primary database |
| `masjidkoi_pgbouncer` | edoburu/pgbouncer:1.22.1 | 6432 | Connection pool (transaction mode) |

PgBouncer settings:
- `POOL_MODE=transaction` — connections returned immediately after each statement
- `MAX_CLIENT_CONN=1000` — max connections from FastAPI
- `DEFAULT_POOL_SIZE=20` — server connections to PostgreSQL
- `IGNORE_STARTUP_PARAMETERS=extra_float_digits,options` — required for asyncpg compatibility

SQLAlchemy uses `NullPool` so it never holds a persistent connection; PgBouncer handles all multiplexing.

---

## What is NOT in Sprint 1

The following are explicitly deferred to Sprint 2 (months 4–6):

- **Donation system** — SSLCommerz/bKash/Nagad integration, webhook HMAC verification, PDF receipt generation
- **Push notifications** — Firebase Cloud Messaging for prayer reminders and announcements
- **Community features** — Follow/unfollow masjids, announcements feed, user reviews
- **Platform analytics** — Charts, heatmaps, export reports
- **User management** — Mobile app user registration, suspend/delete with PDPO compliance
- **Bengali i18n** — i18next integration on the frontend
- **Recurring donations** — Subscription schedules and retry logic

---

## Running Locally

```bash
# 1. Copy env file
cp .env.example .env

# 2. Start all services
docker compose up --build

# 3. Run migrations (separate terminal, direct to postgres)
DATABASE_URL=postgresql://masjidkoi:masjidkoi@localhost:5432/masjidkoi uv run alembic upgrade head

# 4. API docs
open http://localhost:8000/docs

# 5. Health check
curl http://localhost:8000/health
```

---

## Acceptance Criteria for Sprint 1 Sign-Off

- [ ] `GET /health` returns `200` with `"database": "ok"` and `postgis` version
- [ ] Super Admin can log in with email + password + TOTP 2FA
- [ ] Super Admin can create a masjid and assign credentials
- [ ] `GET /masjids/nearby?lat=23.7&lng=90.4&radius_m=2000` returns PostGIS-sorted results
- [ ] Masjid Admin can log in and update their own masjid profile
- [ ] Prayer times are auto-calculated and can be manually overridden
- [ ] All admin write actions are recorded in the audit log
- [ ] All migrations apply cleanly on a fresh PostgreSQL container
