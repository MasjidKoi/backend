# Auth Plan — GoTrue + Role-Based Access

**SRS refs:** FR-T40-072 · FR-T40-073 · FR-T40-074 · FR-T40-075 · FR-T40-076 · FR-T40-077  
**Stack:** GoTrue v2.151.0 · PyJWT · httpx · pyotp

---

## Architecture

```
Browser / Next.js Admin
        │  HTTPS
        ▼
  FastAPI :8000  ← verifies JWT locally (no GoTrue round-trip per request)
        │  HTTP (container network only)
        ▼
  GoTrue :9999   ← issues JWTs, manages users, sends emails, handles TOTP
        │  SQL
        ▼
  PostgreSQL :5432  (schema: auth.*)
```

**Key principle:** GoTrue is an internal service. Clients call FastAPI at `/auth/*`;  
FastAPI proxies to GoTrue and returns the JWT. GoTrue is never publicly exposed.

---

## Role Model

Roles are stored in `app_metadata.role` inside every JWT GoTrue issues.  
`app_metadata` is write-protected — only the service_role token can change it.

```python
class AdminRole(StrEnum):
    PLATFORM_ADMIN  = "platform_admin"   # NGO Super Admin — full access
    MASJID_ADMIN    = "masjid_admin"     # scoped to one masjid
    MADRASHA_ADMIN  = "madrasha_admin"   # scoped to one madrasha
```

### Resource scoping

| Role | app_metadata fields |
|---|---|
| `platform_admin` | `role` only |
| `masjid_admin` | `role` + `masjid_id` (UUID) |
| `madrasha_admin` | `role` + `madrasha_id` (UUID) |

Routes for masjid/madrasha admins **must** enforce `user.masjid_id == path_param`  
(platform_admin bypasses this check).

---

## JWT Structure

GoTrue issues HS256 JWTs. FastAPI verifies them using the shared `GOTRUE_JWT_SECRET`.

```jsonc
{
  "aud": "authenticated",
  "exp": 1234567890,
  "sub": "user-uuid",
  "email": "admin@masjidkoi.com",
  "app_metadata": {
    "provider": "email",
    "role": "platform_admin",    // ← AdminRole enum value
    "masjid_id": null,
    "madrasha_id": null
  },
  "user_metadata": {},
  "role": "authenticated",       // GoTrue internal — ignore in FastAPI
  "aal": "aal2",                 // aal1 = password only, aal2 = password + TOTP
  "session_id": "uuid"
}
```

FastAPI reads `app_metadata.role` and `aal` to gate every protected route.

---

## Authentication Assurance Level (AAL)

GoTrue embeds `aal` in every JWT:

| AAL | Meaning | Who requires it |
|---|---|---|
| `aal1` | Password only | `masjid_admin`, `madrasha_admin` |
| `aal2` | Password + TOTP verified | `platform_admin` (mandatory) |

Platform admins log in with password → get `aal1` JWT → verify TOTP  
→ GoTrue issues new `aal2` JWT → access unlocked.

---

## Login Flows

### Masjid / Madrasha Admin
```
POST /auth/login          body: {email, password}
  → GoTrue /token         returns aal1 JWT
  ← FastAPI returns TokenResponse (aal1)
```
Ready to use immediately — no TOTP required.

### Platform Admin
```
POST /auth/login          body: {email, password}
  → GoTrue /token         returns aal1 JWT
  ← FastAPI returns TokenResponse (aal1)

POST /auth/2fa/verify     body: {factor_id, code}  Authorization: Bearer <aal1 token>
  → GoTrue /factors/{id}/verify
  ← FastAPI returns new TokenResponse (aal2)
```
Only the `aal2` token unlocks `/admin/*` endpoints.

### TOTP Enrollment (first-time setup)
```
POST /auth/2fa/enroll     Authorization: Bearer <aal1 token>
  → GoTrue /factors       returns {factor_id, totp_uri, qr_code}
  ← Frontend renders QR code

POST /auth/2fa/verify     body: {factor_id, code}
  → GoTrue /factors/{id}/verify
  ← Factor activated; session upgraded to aal2
```

---

## Provisioning New Admins

Only a `platform_admin` with `aal2` can create new admin users.

```
POST /auth/admin/invite   body: {email, role, masjid_id?}
  → GoTrue /admin/users   sets app_metadata.role + masjid_id
                          sends invite email to new admin
  ← returns {gotrue_user_id, email, role}
```

The invitee clicks the email link → sets their password → can now log in.  
For `masjid_admin`, `masjid_id` is embedded in all future JWTs automatically.

---

## API Endpoints

| Method | Path | Auth required | GoTrue call |
|---|---|---|---|
| POST | `/auth/login` | None | `POST /token?grant_type=password` |
| POST | `/auth/refresh` | None | `POST /token?grant_type=refresh_token` |
| POST | `/auth/logout` | Bearer (any) | `POST /logout` |
| POST | `/auth/2fa/enroll` | Bearer aal1, platform_admin | `POST /factors` |
| POST | `/auth/2fa/verify` | Bearer aal1 | `POST /factors/{id}/verify` |
| POST | `/auth/password/reset` | None | `POST /recover` |
| POST | `/auth/admin/invite` | Bearer aal2, platform_admin | `POST /admin/users` |

---

## FastAPI Dependencies

```
get_current_user        → verifies JWT, returns CurrentUser (all roles)
require_platform_admin  → role=platform_admin AND aal=aal2
require_masjid_admin    → role in [platform_admin, masjid_admin]
require_madrasha_admin  → role in [platform_admin, madrasha_admin]
require_any_admin       → any valid role
```

Route example:
```python
@router.patch("/masjids/{id}")
async def update_masjid(
    id: UUID,
    user: CurrentUser = Depends(require_masjid_admin),
):
    # Scope check — platform_admin can update any masjid
    if not user.is_platform_admin and user.masjid_id != id:
        raise HTTPException(403, "Access restricted to own masjid")
    ...
```

---

## File Layout

```
app/
  models/
    enums.py              ← AdminRole, AuthAssuranceLevel, MasjidStatus, ...
  schemas/
    auth.py               ← LoginRequest, TokenResponse, AdminInviteRequest, ...
  core/
    security.py           ← decode_token(), CurrentUser dataclass
    config.py             ← GOTRUE_JWT_SECRET, GOTRUE_URL, GOTRUE_SERVICE_ROLE_KEY
  dependencies/
    auth.py               ← get_current_user, require_platform_admin, ...
  services/
    gotrue_client.py      ← async httpx wrapper for GoTrue admin API
  routers/
    auth.py               ← /auth/* endpoints

scripts/
  gen_service_token.py    ← one-time: generates GOTRUE_SERVICE_ROLE_KEY
  init-db.sql             ← creates auth schema on first postgres start

docker-compose.yml        ← gotrue service added alongside api/postgres/pgbouncer
.env.example              ← GOTRUE_JWT_SECRET, GOTRUE_SERVICE_ROLE_KEY, SMTP_*
```

---

## Initial Setup (one-time)

```bash
# 1. Generate JWT secret
export GOTRUE_JWT_SECRET=$(openssl rand -base64 32)
echo "GOTRUE_JWT_SECRET=$GOTRUE_JWT_SECRET" >> .env

# 2. Generate service role token
uv run python scripts/gen_service_token.py >> .env

# 3. Start everything
docker compose up --build

# 4. Run migrations
DATABASE_URL=postgresql://masjidkoi:masjidkoi@localhost:5432/masjidkoi \
  uv run alembic upgrade head

# 5. Create the first platform admin (using service role directly)
curl -X POST http://localhost:9999/admin/users \
  -H "Authorization: Bearer $GOTRUE_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "superadmin@masjidkoi.com",
    "password": "ChangeMe123!",
    "email_confirm": true,
    "app_metadata": {"role": "platform_admin"}
  }'

# 6. Log in, enroll TOTP, verify
curl -X POST http://localhost:8000/auth/login \
  -d '{"email":"superadmin@masjidkoi.com","password":"ChangeMe123!"}'
# → aal1 token

curl -X POST http://localhost:8000/auth/2fa/enroll \
  -H "Authorization: Bearer <aal1_token>"
# → {factor_id, totp_uri, qr_code}  — scan QR in authenticator app

curl -X POST http://localhost:8000/auth/2fa/verify \
  -H "Authorization: Bearer <aal1_token>" \
  -d '{"factor_id":"<id>","code":"123456"}'
# → aal2 token — now has full platform_admin access
```

---

## What Remains (Sprint 2)

- [ ] Audit log middleware — intercept every admin write and append to `audit_logs`
- [ ] Session management — store active sessions; enforce 30-min inactivity (FR-T40-076)
- [ ] Password change on first login (invite flow)
- [ ] TOTP enforcement at the DB level — flag `mfa_required` on platform_admin rows so   
      it cannot be bypassed even with a manually crafted token
- [ ] IP allowlist for platform_admin (FR-T40-078)
- [ ] Email templates for invite / password reset (SMTP config)
