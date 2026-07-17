# MasjidKoi Backend — Deep Codebase Audit

> Multi-agent audit across 8 dimensions with adversarial verification of every finding.
> Generated 2026-07-11. Scope: whole repository (application code + production deployment).

## How this was produced

Eight independent auditor agents each swept one dimension — deployment, auth/authz, data-exposure/web-security, concurrency/deadlocks, separation-of-concerns, payments, data-integrity/migrations, and general correctness. **Every raised finding was then handed to a separate adversarial verifier** instructed to *refute* it (default to REFUTED if unsubstantiated). A final completeness critic hunted for cross-cutting issues the dimension finders missed.

- **Findings raised:** 26
- **Survived verification:** 25 (1 refuted and dropped)
- **Unverified critic leads:** 4 (listed separately at the end)

## Severity summary

| Severity | Count |
|---|---|
| 🟠 High | 4 |
| 🟡 Medium | 12 |
| 🔵 Low | 7 |
| ⚪ Info | 2 |

## Findings index

| # | Sev | Dimension | Verdict | Finding |
|---|---|---|---|---|
| 1 | 🟠 high | Authentication & Authorization | CONFIRMED | Platform-admin MFA (aal2/TOTP) is never enforced — every destructive admin operation is reachable with a password-only session |
| 2 | 🟠 high | Data Integrity & Migrations | CONFIRMED | Account purge leaves identifying PII (email, display name) in local content tables it claims to anonymise |
| 3 | 🟠 high | Data Integrity & Migrations | CONFIRMED | Account purge never deletes the GoTrue identity, so the user's login email/phone survive the deletion window indefinitely |
| 4 | 🟠 high | Deployment & Infrastructure | CONFIRMED | Production CORS allow_origins is hardcoded to a domain that does not exist in the prod deployment (admin.masjidkoi.me vs app.masjidkoi.me), so the production admin panel is blocked from calling the API |
| 5 | 🟡 medium | Authentication & Authorization | CONFIRMED | Public masjid endpoints leak moderation state: suspended/removed masjids are enumerable and suspension_reason is exposed to anonymous users |
| 6 | 🟡 medium | Authentication & Authorization | CONFIRMED | OTP verify lockout and send-caps are fully bypassed when Redis is unavailable, and verify has no per-IP throttle |
| 7 | 🟡 medium | General Correctness | CONFIRMED | Campaign analytics endpoint always returns donor_count=0 and average_donation=None |
| 8 | 🟡 medium | Data Exposure & Web Security | PLAUSIBLE | Public /payments/sslcommerz/redirect endpoint has no rate limit and triggers a 15s outbound gateway call + DB row lock on attacker-controlled input |
| 9 | 🟡 medium | Data Exposure & Web Security | CONFIRMED | Per-IP rate limiting and request logging key on the reverse-proxy IP, collapsing all clients into one bucket |
| 10 | 🟡 medium | Deployment & Infrastructure | CONFIRMED | .dockerignore excludes only .env, not .env.production, so real production secrets get baked into the migrate image built by docker-compose.prod.yml |
| 11 | 🟡 medium | Deployment & Infrastructure | CONFIRMED | MinIO photos bucket is granted anonymous public-read in prod, but community/submission photos are written there in PENDING moderation status, so unmoderated content is publicly served the moment it is uploaded |
| 12 | 🟡 medium | Payments & Financial Correctness | CONFIRMED | create_pending holds a DB transaction open across the up-to-15s SSLCommerz create_session HTTP call, pinning a PgBouncer server connection per in-flight checkout |
| 13 | 🟡 medium | Separation of Concerns | CONFIRMED | Route executes raw SQL directly against the database, bypassing repository and service layers |
| 14 | 🟡 medium | Separation of Concerns | CONFIRMED | Admin routes instantiate repositories directly and call them from the HTTP layer, bypassing services |
| 15 | 🟡 medium | Separation of Concerns | CONFIRMED | Route commits the session and writes audit log directly instead of the service layer |
| 16 | 🟡 medium | Separation of Concerns | CONFIRMED | Route performs external HTTP orchestration to GoTrue and returns an unvalidated dict |
| 17 | 🔵 low | Authentication & Authorization | CONFIRMED | Co-admins have full masjid_admin parity — any co-admin can revoke the inviting admin and invite further admins |
| 18 | 🔵 low | General Correctness | CONFIRMED | list_goals issues an N+1 query — one progress query per goal |
| 19 | 🔵 low | General Correctness | CONFIRMED | Donation history keyset pagination emits a next_cursor for the final full page (phantom empty page) |
| 20 | 🔵 low | Data Exposure & Web Security | CONFIRMED | Unauthenticated /health endpoint leaks raw database exception strings |
| 21 | 🔵 low | Data Exposure & Web Security | CONFIRMED | CORS allows localhost dev origins with credentials in the single production config |
| 22 | 🔵 low | Deployment & Infrastructure | CONFIRMED | Dev frontend is built to call the API on port 8001, but the API is published on 8000 — no service listens on 8001 |
| 23 | 🔵 low | Deployment & Infrastructure | CONFIRMED | No healthcheck on the api service; Caddy (prod) and frontend depend on it by start-order only, so the reverse proxy can begin routing before uvicorn is ready |
| 24 | ⚪ info | Concurrency & Deadlocks | CONFIRMED | refund() holds a donation row under SELECT ... FOR UPDATE across the external SSLCommerz gateway HTTP call (up to 15s), pinning a PgBouncer transaction-mode server connection for the duration |
| 25 | ⚪ info | Concurrency & Deadlocks | PLAUSIBLE | Recurring-donation nudge sweep reads due rows without a row lock, unlike the stale-pending sweep, so it relies solely on the fail-open Redis lock to avoid duplicate nudges/double-advance |

---

## Verified findings

### 1. 🟠 `HIGH` — Platform-admin MFA (aal2/TOTP) is never enforced — every destructive admin operation is reachable with a password-only session

**Dimension:** Authentication & Authorization · **Category:** `mfa-not-enforced` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/dependencies/auth.py:68-82`
- `app/core/security.py:69-71`
- `app/routers/auth.py:218`

**What's wrong:** require_platform_admin only checks user.role == PLATFORM_ADMIN; the aal2 check is commented out ("TODO: re-enable aal2 check when TOTP is stable", lines 79-81). Nothing anywhere in the codebase reads CurrentUser.aal / has_mfa to gate access — a repo-wide grep for aal2/AAL2/has_mfa finds only the property definition, the decode logic, and doc strings, never an enforcement call. TOTP enrollment (/auth/2fa/enroll) is entirely optional; a platform admin who never enrolls has an aal1 session that passes every guard. This directly contradicts the documented invariant (auth.py:218 "Platform admins MUST hold an aal2 token to access /admin/* endpoints", enums.py:31, and the /auth/admin/invite doc "Requires platform_admin with aal2").

**Impact:** An attacker who obtains only a platform admin's email+password (phishing, credential stuffing, reused password) gets full access with no second factor: refund donations (donation_service.refund), record disbursements (money leaving the platform), ban/delete app users (admin_user_service), suspend/merge masjids, bulk-import and bulk-export the entire masjid directory, and invite new platform admins. The intended TOTP barrier for these money- and account-destructive operations does not exist.

**Recommendation:** Re-enable the aal2 assertion in require_platform_admin (raise 403 unless user.aal == AAL2), and make TOTP enrollment mandatory for platform admins (block sensitive endpoints for un-enrolled admins rather than silently allowing aal1). If aal2 truly cannot be enforced yet, remove the false 'MUST have aal2' claims from the docs so the security posture is not misrepresented.

<details><summary>Adversarial verification notes</summary>

The defect is real and reachable exactly as described.

1. app/dependencies/auth.py:68-82 — require_platform_admin only checks `user.role != AdminRole.PLATFORM_ADMIN`. The aal2 gate is commented out (lines 79-81: "TODO: re-enable aal2 check when TOTP is stable" followed by the commented `if user.aal != AuthAssuranceLevel.AAL2` raise). The docstring at line 72 even states "aal1 or aal2 accepted (TOTP disabled for now)". So a valid platform_admin JWT with aal=aal1 passes the guard.

2. app/core/security.py:68-71 — `has_mfa` property is defined but a repo-wide grep (`has_mfa|aal2|AAL2|\.aal|require_aal|require_mfa|require_2fa`) finds NO enforcement call anywhere. All hits are: the property definition, the decode logic in security.py (which defaults raw_aal to "aal1", line 141), the enums, and docstrings/summaries. No dependency, service, or route ever reads `user.aal` or `user.has_mfa` to gate access. `has_mfa` is dead code.

3. TOTP enrollment is optional — /auth/2fa/enroll (auth.py) and /2fa/verify (auth.py:211-231) are opt-in; nothing forces a platform admin to enroll. An admin who never enrolls holds only aal1 tokens, which pass every guard.

4. The documented invariant is explicitly contradicted: auth.py:218 ("Platform admins MUST hold an aal2 token to access /admin/* endpoints"), enums.py:31 ("Platform admins MUST have AAL2 to access sensitive endpoints"), auth.py:294 (invite "Requires platform_admin with aal2"), and route summaries in masjids.py:69,325,338 ("platform_admin + aal2").

5. Impact is real: require_platform_admin gates genuinely destructive/money-movement operations — donations.py:309/325/360 (refund, disbursement), admin.py (ban/delete app users, ~278), masjids.py (suspend/verify), masjid_submissions.py, and auth.py:299 (invite new platform admin). All are reachable with a password-only aal1 session.

This is a defense-in-depth control that is intentionally disabled and documented as required. It is not a full auth bypass (a valid admin credential + correct role is still required), so exploitation presupposes credential compromise (phishing/credential-stuffing/reuse) — but for money-movement and admin-invite operations the intended second-factor barrier simply does not exist. Severity high is appropriate: the missing factor protects financial operations and account-destructive actions, and the gap is silent (the guard's name and docs imply protection that isn't there).

</details>

---

### 2. 🟠 `HIGH` — Account purge leaves identifying PII (email, display name) in local content tables it claims to anonymise

**Dimension:** Data Integrity & Migrations · **Category:** `pii-leak` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/repositories/account_purge_repository.py:86-108`
- `app/models/support_ticket.py:39`
- `app/models/masjid_review.py:43`
- `app/services/masjid_review_service.py:69`

**What's wrong:** AccountPurgeRepository.anonymize_user re-keys user_id -> pseudonym on the _REMAP_TABLES and, per its docstring, is supposed to 'blank direct-PII contact fields'. But it only blanks PII on the Donation row (donor_name/donor_email, lines 93-97). Every other remapped table keeps its identity snapshot untouched: the loop at lines 98-103 sets ONLY the identity UUID column. Two of those tables carry direct PII copied from the user's identity: SupportTicket.user_email (support_ticket.py:39, populated from user.email in support_ticket_service.py:29) and MasjidReview.reviewer_display_name (masjid_review.py:43, populated from the user's profile display_name in masjid_review_service.py:69/81). After a purge the profile tombstone blanks display_name, but the review row still carries the person's name and is shown publicly, and the support ticket still carries their email address.

**Impact:** A user requests deletion; 30 days later the purge job runs and 'anonymises' them. Their real display name still appears on every public masjid review they wrote (reviewer_display_name never cleared), and their email address still sits in support_tickets.user_email. The account is still trivially identifiable, contradicting the anonymisation guarantee and the 'data purged within 30 days' promise in user_service.delete_me.

**Recommendation:** In anonymize_user, blank the PII snapshot columns alongside the re-key: UPDATE support_tickets SET user_id=pseudonym, user_email=NULL, and UPDATE masjid_reviews SET user_id=pseudonym, reviewer_display_name=NULL. Audit every _REMAP_TABLES entry for other snapshotted identity/free-text PII (e.g. support_tickets.subject/description) and null it in the same statement.

<details><summary>Adversarial verification notes</summary>

The defect is real and reachable exactly as described.

1. account_purge_repository.py:98-103 — the remap loop executes `update(model).values(**{column: pseudonym})`, setting ONLY the identity UUID column. It does not touch any snapshot-PII columns. The only table that gets PII blanked is Donation (lines 93-97: donor_name=None, donor_email=None). Both SupportTicket (line 53) and MasjidReview (line 45) are in _REMAP_TABLES, so their PII snapshot columns survive untouched.

2. MasjidReview.reviewer_display_name exists (masjid_review.py:43) and is populated from the user's profile display_name on both create and edit (masjid_review_service.py:63,69,81). It is exposed in MasjidReviewResponse (masjid_review.py schema:27) and the list endpoint is explicitly documented as PUBLIC (routers/masjids.py:441 "List reviews for a masjid — public, paginated", no auth dependency on the GET). So after purge the ex-user's real display name remains visible on every public review they wrote.

3. SupportTicket.user_email exists (support_ticket.py:39) and is populated directly from user.email (support_ticket_service.py:29). It is returned in the admin response (_to_admin_response, support_ticket_service.py:108) and used to send resolution emails (line 79-81). After purge this real email address persists in support_tickets.user_email.

4. mark_purged (account_purge_repository.py:110-117) only blanks the profile tombstone (display_name, madhab, profile_photo_url) — it does not touch the review/ticket snapshot copies. So the docstring's claim to "blank direct-PII contact fields" is only honored for Donation, contradicting the anonymisation guarantee and the 30-day purge promise.

The one nuance: the support-ticket email leak is admin-visible only, not public, whereas the review display-name leak is fully public. But both are genuine PII retained after a purge that claims to anonymise, so the finding stands.

</details>

---

### 3. 🟠 `HIGH` — Account purge never deletes the GoTrue identity, so the user's login email/phone survive the deletion window indefinitely

**Dimension:** Data Integrity & Migrations · **Category:** `deletion-gap` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/services/account_purge_service.py:64-71`
- `app/repositories/account_purge_repository.py:1-18`
- `app/services/gotrue_client.py:307-316`
- `app/services/user_service.py:178-183`

**What's wrong:** delete_me tells the user 'Your data will be permanently purged within 30 days' (user_service.py:180). The scheduled consumer, AccountPurgeService.purge_profile, only touches LOCAL tables: anonymize_user re-keys/blanks local rows and mark_purged tombstones the profile. It never severs the GoTrue auth identity, even though the purge module docstring explicitly notes 'users live in GoTrue'. A working gotrue.delete_user(id) exists (gotrue_client.py:307) and is already used by co_admin_invite_service (167,184) and admin_user_service (90) — but the consumer purge never calls it. The primary PII (the login email/phone, and any name in auth metadata) therefore lives on in GoTrue's auth.users forever after a deletion request.

**Impact:** A user deletes their account. Local content is anonymised at day 30, but their email/phone remains in GoTrue indefinitely — they still exist as a resolvable identity, can still be looked up by email, and the login credential is never revoked. This breaks the explicit 'permanently purged within 30 days' commitment and is a GDPR/PDPA-style right-to-erasure violation for the most sensitive field (the account identifier).

**Recommendation:** In purge_profile, after the local anonymise+tombstone commit succeeds, call gotrue.delete_user(profile.user_id) (best-effort, logged and retried on failure so a GoTrue outage doesn't silently skip it). Order it after the local commit so a GoTrue failure leaves purged_at unset and the account is retried on the next sweep; or track a separate gotrue_purged flag so the two steps are independently idempotent.

<details><summary>Adversarial verification notes</summary>

Traced the entire deletion path. user_service.delete_me (user_service.py:165-183) only soft-deletes and emails a "permanently purged within 30 days" promise; it never imports or calls the GoTrue client (grep confirms zero gotrue references in user_service.py). The scheduled consumer AccountPurgeService.purge_profile (account_purge_service.py:64-71) calls only repo.anonymize_user + mark_purged + commit, and the service imports only settings/UserProfile/AccountPurgeRepository — no GoTrue client. AccountPurgeRepository.anonymize_user and mark_purged (account_purge_repository.py:86-117) exclusively touch local Postgres tables (re-key content rows to a pseudonym, blank donor_name/donor_email, hard-delete device_tokens/recurring_schedules, and tombstone the profile). The scheduler (scheduler.py:140-142) invokes only AccountPurgeService(db).run_due(). Meanwhile gotrue.delete_user (gotrue_client.py:307-314) is a working DELETE /admin/users/{id} that IS called by co_admin_invite_service.py:167,184 and admin_user_service.py:90 — proving the capability exists and works — but it is never invoked anywhere in the self-deletion or purge flow. Therefore the GoTrue auth.users identity (login email/phone and auth metadata) survives indefinitely after the 30-day window, the credential is never banned/revoked, and the "permanently purged within 30 days" commitment is broken for the most sensitive PII. The purge repo docstring even acknowledges "users live in GoTrue," underscoring the omission.

</details>

---

### 4. 🟠 `HIGH` — Production CORS allow_origins is hardcoded to a domain that does not exist in the prod deployment (admin.masjidkoi.me vs app.masjidkoi.me), so the production admin panel is blocked from calling the API

**Dimension:** Deployment & Infrastructure · **Category:** `cors-misconfiguration` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/main.py:140-146`
- `docker-compose.prod.yml:289`
- `docker-compose.prod.yml:149`
- `docker-compose.prod.yml:44`
- `.env.production.example:20`
- `.env.production.example:36`

**What's wrong:** CORSMiddleware is configured with a static allow_origins list: [http://localhost:3000, http://localhost:3001, http://127.0.0.1:3000, https://admin.masjidkoi.me]. There is no env-driven CORS origins setting in app/core/config.py, so this list cannot be overridden per environment. In production the admin panel is served at APP_DOMAIN=app.masjidkoi.me (Caddyfile {$APP_DOMAIN} -> frontend:3000; FRONTEND_URL=https://app.masjidkoi.me; frontend build arg NEXT_PUBLIC_API_URL=https://api.masjidkoi.me). The browser Origin will therefore be https://app.masjidkoi.me, which is NOT in the allow list; only the never-deployed host https://admin.masjidkoi.me is allowed.

**Impact:** In production, a browser at https://app.masjidkoi.me issuing a request/preflight to https://api.masjidkoi.me is rejected by CORS (no Access-Control-Allow-Origin match). Every cross-origin API call from the admin panel fails, effectively breaking the web admin against the API. Because origins are hardcoded (not env-configurable), the same image cannot be fixed via .env.production.

**Recommendation:** Drive allowed origins from a setting (e.g. CORS_ORIGINS in config.py, populated from .env/.env.production) and include the real production origin https://app.masjidkoi.me (and any others). Remove/replace the stale admin.masjidkoi.me entry.

<details><summary>Adversarial verification notes</summary>

The defect is real and grounded in the code as shipped.

1. CORS is hardcoded and not env-overridable. app/main.py:138-149 adds exactly one CORSMiddleware with a literal `allow_origins` list: `http://localhost:3000`, `http://localhost:3001`, `http://127.0.0.1:3000`, `https://admin.masjidkoi.me` (main.py:140-145). A full grep confirms this is the ONLY CORS config in the codebase, and app/core/config.py has NO origin/CORS/FRONTEND_URL setting (both greps returned nothing), so the list cannot be changed per environment without rebuilding the image.

2. The canonical production config uniformly targets app.masjidkoi.me, not admin.masjidkoi.me. In .env.production.example: APP_DOMAIN=app.masjidkoi.me (line 20), FRONTEND_URL=https://app.masjidkoi.me (line 36), GOTRUE_URI_ALLOW_LIST all point at https://app.masjidkoi.me (line 38). Caddyfile:30-33 routes `{$APP_DOMAIN}` (= app.masjidkoi.me) to frontend:3000. docker-compose.prod.yml:289 builds the frontend with NEXT_PUBLIC_API_URL=https://${API_DOMAIN} = https://api.masjidkoi.me, a browser-exposed (NEXT_PUBLIC_*) base URL, so the admin panel served from app.masjidkoi.me makes client-side cross-origin calls to api.masjidkoi.me.

3. Mismatch is genuine and reachable. A browser at https://app.masjidkoi.me issuing credentialed cross-origin requests/preflights to https://api.masjidkoi.me will present Origin: https://app.masjidkoi.me, which is not in the allow list (only the never-referenced admin.masjidkoi.me is). With allow_credentials=True, Starlette's CORSMiddleware does exact-origin matching and will not emit a matching Access-Control-Allow-Origin, so the browser blocks the responses. There is no server-side proxy in the repo that would make CORS moot — the frontend calls the API directly from the browser via the public env var. The repo's own config is internally inconsistent (every other file says app.*, CORS uniquely says admin.*).

I considered REFUTED paths: (a) maybe admin.masjidkoi.me is the real host — refuted, because the entire shipped prod config template and reverse-proxy routing use app.masjidkoi.me; (b) maybe Next.js proxies server-side making CORS irrelevant — refuted, NEXT_PUBLIC_API_URL is a browser-exposed direct API URL. The defect stands.

Severity: I keep it high. As-shipped, the production admin panel's cross-origin browser calls to the API are blocked, breaking core admin functionality, and the fix requires an image rebuild (not just an env edit) because the origins are hardcoded. This is a genuine, deployment-blocking config defect rather than a hypothetical.

</details>

---

### 5. 🟡 `MEDIUM` — Public masjid endpoints leak moderation state: suspended/removed masjids are enumerable and suspension_reason is exposed to anonymous users

**Dimension:** Authentication & Authorization · **Category:** `public-data-exposure` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** medium

**Locations:**
- `app/routers/masjids.py:272-293`
- `app/repositories/masjid_repository.py:197-236`
- `app/routers/masjids.py:296-305`
- `app/services/masjid_service.py:343`

**What's wrong:** GET /masjids (no auth dependency, labeled 'public') delegates to MasjidService.list_for_admin → repo.list_for_admin, which applies NO default status filter (masjid_repository.py:208-209 only filters when a status is explicitly passed). Unlike get_nearby/search which force Masjid.status == ACTIVE, this endpoint returns masjids in ALL states, and accepts ?status=Suspended (or Removed/Pending) so an unauthenticated caller can enumerate exactly which masjids are suspended or removed. Separately, the public GET /masjids/{id} (masjids.py:301 → masjid_service.get_by_id) performs no status check and _to_response includes suspension_reason (masjid_service.py:343), so anyone can read the internal moderation note for any suspended masjid.

**Impact:** Any anonymous client can list all Suspended/Removed masjids and retrieve the admin-authored suspension_reason (internal moderation rationale) for each — internal operational/moderation state disclosed to the public. This is data that the Active-only spatial/search endpoints deliberately hide.

**Recommendation:** Make GET /masjids public listing default to status=Active only (or require platform_admin for non-Active filters), and drop suspension_reason from the public MasjidResponse (or 404/redact non-Active masjids on the public get_by_id path), exposing it only to platform_admin / the owning masjid_admin.

<details><summary>Adversarial verification notes</summary>

Both sub-claims verified in source. (1) list_masjids router (app/routers/masjids.py:272-293) is public — no user/auth dependency and no router-level dependencies (router defined at line 55 as APIRouter(prefix, tags) only). It forwards status_filter unchanged through MasjidService.list_for_admin (app/services/masjid_service.py:479-496) into MasjidRepository.list_for_admin, which applies a status filter ONLY when one is explicitly provided (app/repositories/masjid_repository.py:207-209) and otherwise returns masjids in all states. There is no default Masjid.status==ACTIVE guard, unlike get_nearby/search. So an anonymous client can enumerate Suspended/Removed/Pending masjids via ?status=. (2) The public get_masjid endpoint (app/routers/masjids.py:296-305) calls get_by_id (app/services/masjid_service.py:369-376) with no status check, and _to_response populates suspension_reason=masjid.suspension_reason (line 343) into the MasjidResponse that is returned. So the admin-authored suspension_reason is disclosed to any anonymous caller. Both paths are reachable with no authentication.

</details>

---

### 6. 🟡 `MEDIUM` — OTP verify lockout and send-caps are fully bypassed when Redis is unavailable, and verify has no per-IP throttle

**Dimension:** Authentication & Authorization · **Category:** `otp-rate-limit-gap` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/services/otp_auth_service.py:112-116`
- `app/services/otp_auth_service.py:83-93`
- `app/services/otp_auth_service.py:141-167`

**What's wrong:** All OTP abuse controls are gated on `if self.redis is not None`. When Redis is down (a state the service explicitly tolerates — otp_auth.py:12-15 constructs the service with redis=None), verify_otp skips the MAX_VERIFY_ATTEMPTS lockout entirely (lines 112-116) and request_otp skips both the 60s cooldown and the per-email/per-IP hourly send caps (lines 83-93), delegating every attempt straight to GoTrue. Even with Redis up, the wrong-guess lockout is keyed only per-email (_attempts_key(email)); there is no per-IP cap on the verify endpoint, only on send.

**Impact:** In degraded (Redis-down) mode the application-layer brute-force and send-flood protection for the 6-digit passwordless login code disappears: an attacker can hammer /auth/otp/verify against a target email and spam /auth/otp/request with no cooldown or cap, leaving only GoTrue's own (weaker/unverified-here) limits between the attacker and account takeover. The graceful-degradation design silently removes the primary control on the most exposed authentication surface.

**Recommendation:** Fail closed on the security-critical checks when Redis is unavailable (reject verify / refuse to send rather than skipping the lockout and caps), or back the verify-attempt counter and per-IP throttle with a durable store. Add a per-IP verify limiter alongside the existing per-email lockout.

<details><summary>Adversarial verification notes</summary>

All factual claims verified in source. (1) Every OTP abuse control is gated behind `if self.redis is not None`: cooldown + send-caps in request_otp (otp_auth_service.py:83-93), the MAX_VERIFY_ATTEMPTS lockout in verify_otp (112-116), and attempt-burning in _classify_verify_failure (143-147, which returns a plain invalid_code and no lockout when redis is None). (2) The service genuinely runs with redis=None: dependencies/otp_auth.py:14 does `getattr(request.app.state, "redis", None)` and hands it to OtpAuthService; the module docstring (19-21) documents this degraded mode. So when Redis is down, request_otp goes straight to gotrue.send_email_otp with no cooldown/cap and verify_otp goes straight to gotrue.verify_email_otp with no lockout. (3) The verify endpoint has no per-IP throttle at all — verify_otp(email, code) takes no IP (service:109), the route (auth.py:117-121) never even computes client_ip (contrast request_otp at auth.py:100) and attaches no make_rate_limiter dependency; the sole verify control is _attempts_key(email), keyed per-email only (line 114). The reusable IP limiter in app/core/rate_limit.py is not wired into either OTP route. The only fallback in degraded mode is GoTrue's own unconfigured/unverified limits, exactly as the finding states. Severity medium rather than high because the single-target brute-force path is fully open only when Redis is down, and with Redis up the per-email 5-attempts lockout still bounds per-target guessing.

</details>

---

### 7. 🟡 `MEDIUM` — Campaign analytics endpoint always returns donor_count=0 and average_donation=None

**Dimension:** General Correctness · **Category:** `stubbed-analytics` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/services/masjid_campaign_service.py:163`
- `app/services/masjid_campaign_service.py:164`
- `app/routers/campaigns.py:67`

**What's wrong:** get_analytics() computes progress_pct and days_remaining from the campaign row but hardcodes donor_count=0 and average_donation=None instead of deriving them from donation data. The endpoint is documented as "Campaign performance analytics (masjid_admin)" and the response schema (app/schemas/masjid_campaign.py:71-72) declares donor_count: int and average_donation: Decimal | None as real fields. The underlying data is readily available: DonationRepository.campaign_donor_ids(campaign_id) already returns the distinct completed-donation donor set (app/repositories/donation_repository.py:131), and completed gross/net sums per campaign are trivially queryable.

**Impact:** Any masjid admin opening a campaign's analytics screen sees 0 donors and no average donation regardless of how many people actually donated to the campaign. The numbers are simply wrong (always zero/null), directly contradicting the endpoint's stated purpose.

**Recommendation:** Compute donor_count from the count of distinct completed donors for the campaign (e.g. len(await self.repo... / donation_repo.campaign_donor_ids) or a dedicated COUNT DISTINCT query) and average_donation from SUM(gross)/COUNT of completed campaign donations (None when there are no completed donations).

<details><summary>Adversarial verification notes</summary>

Read app/services/masjid_campaign_service.py get_analytics() (lines 136-165). The method computes progress_pct (line 152) and days_remaining (line 153) from the campaign row, but the CampaignAnalyticsResponse is constructed with literal donor_count=0 (line 163) and average_donation=None (line 164). Nothing else populates these; there is no downstream override — the router (app/routers/campaigns.py:71-77) simply returns the service result unmodified, and the response schema (app/schemas/masjid_campaign.py:71-72) declares donor_count: int and average_donation: Decimal | None as first-class fields with from_attributes=False, so no ORM fallback fills them.\n\nThe endpoint is reachable: GET /{masjid_id}/campaigns/{campaign_id}/analytics is a live route guarded by require_masjid_admin, summary \"Campaign performance analytics (masjid_admin)\". Any admin hitting it gets donor_count=0 and average_donation=null regardless of actual donations.\n\nThe data is indeed readily available: DonationRepository.campaign_donor_ids(campaign_id) (app/repositories/donation_repository.py:131-142) already returns the distinct COMPLETED-donation donor set via a GROUP BY on user_id — donor_count is just len() of that, and average is derivable from campaign.raised_amount / donor_count or a sum query. So the values are trivially computable yet hardcoded. The output is unconditionally wrong (always 0/null) contradicting the endpoint's stated purpose. Not a hypothetical; the literals are in the code path.\n\nSeverity medium is appropriate: it is a functional-correctness defect on an admin analytics screen (wrong displayed numbers), not a security or data-integrity issue — no corruption, no auth bypass, limited to one read endpoint's two fields.

</details>

---

### 8. 🟡 `MEDIUM` — Public /payments/sslcommerz/redirect endpoint has no rate limit and triggers a 15s outbound gateway call + DB row lock on attacker-controlled input

**Dimension:** Data Exposure & Web Security · **Category:** `missing-rate-limit-expensive-public-endpoint` · **Verifier verdict:** PLAUSIBLE · **Reporter confidence:** high

**Locations:**
- `app/routers/payments.py:88`
- `app/routers/payments.py:98`
- `app/routers/payments.py:115`
- `app/services/donation_service.py:299`
- `app/services/sslcommerz_gateway.py:193`

**What's wrong:** sslcommerz_redirect accepts GET and POST, is fully unauthenticated, and — unlike the sibling /ipn endpoint (which has make_rate_limiter(limit=120,...) at payments.py:35/47) — carries NO rate-limiter dependency. On a POST with outcome=success and any non-empty val_id+tran_id, it calls service.complete_from_ipn (payments.py:115-117), which after a uuid parse and a PENDING-row lookup invokes gateway.validate_ipn(val_id) — an outbound httpx GET to SSLCommerz with a 15s timeout (sslcommerz_gateway.py:37,209-213). The tran_id path also takes a FOR UPDATE row lock (donation_service.py:305). An attacker only needs any donation_id that exists in PENDING (his own donation, or a guessed/known UUID) to make each request block a worker on up to 15s of outbound I/O and hold a DB lock.

**Impact:** An unauthenticated attacker sends a flood of POST /payments/sslcommerz/redirect/success requests with a valid PENDING tran_id and any val_id; each one opens an outbound connection to SSLCommerz and holds it ~15s while taking a row lock, exhausting the single-worker uvicorn's concurrency and the PgBouncer pool, and hammering the payment gateway from the server (request amplification). The genuine IPN path is protected by a limiter; this equivalent-cost path is not.

**Recommendation:** Attach the same _ipn_limiter (or a dedicated redirect limiter) to sslcommerz_redirect, and/or skip the redirect-path complete_from_ipn call entirely (the doc comment already states the server-to-server IPN is the authoritative completer), relying on the client status poll rather than doing gateway validation on the unauthenticated redirect.

<details><summary>Adversarial verification notes</summary>

Core existence claim holds: sslcommerz_redirect (app/routers/payments.py:88-98) is unauthenticated, accepts GET+POST, and has NO rate limiter, while the sibling /ipn does (payments.py:35,46). A success POST with non-empty val_id+tran_id calls complete_from_ipn (payments.py:115-117) which reaches gateway.validate_ipn — an outbound httpx GET with a 15s timeout (sslcommerz_gateway.py:37,209-213). That much is reachable, but only when the donation exists AND is PENDING (donation_service.py:267-288 short-circuits missing/COMPLETED/terminal before the gateway call).

However, the finding's stated IMPACT mechanisms are largely refuted by mitigations present in the code: (1) There is NO DB row lock held across the 15s gateway call — complete_from_ipn explicitly commits the read txn to release the PgBouncer connection BEFORE validate_ipn (donation_service.py:290-299, with a comment stating this is done specifically to avoid pinning a pool connection across the 15s I/O); the FOR UPDATE lock (line 305) is taken AFTER the gateway call and held briefly. So no 15s lock and no PgBouncer-pool pinning during I/O. (2) validate_ipn is awaited non-blocking async httpx, so it does not "block a worker" — the async event loop services many concurrent awaits; the single-worker-concurrency-exhaustion framing does not apply to async endpoints. (3) The fail/cancel branch (fail_from_redirect, donation_service.py:429-451) makes no gateway call at all, so it is not an equivalent-cost path.

Residual real risk: the endpoint lacks the rate limit its sibling has and does issue an outbound gateway call for an unauthenticated caller. Because get_by_id (line 267) reads PENDING before the FAILED commit (~15s later, line 315), many concurrent requests with one self-created PENDING tran_id all pass the check and fan out into concurrent validate_ipn calls (open sockets/FDs + amplification toward SSLCommerz) until the row self-heals to FAILED and later requests short-circuit (line 276). UUIDs are unguessable, so the attacker must use their own donation. So a genuine missing-rate-limit gap exists, but the DoS impact is much weaker than described.

</details>

---

### 9. 🟡 `MEDIUM` — Per-IP rate limiting and request logging key on the reverse-proxy IP, collapsing all clients into one bucket

**Dimension:** Data Exposure & Web Security · **Category:** `ineffective-rate-limit` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/core/rate_limit.py:20`
- `app/core/rate_limit.py:21`
- `app/core/middleware.py:29`
- `Caddyfile:19`
- `Dockerfile:73`

**What's wrong:** make_rate_limiter builds its Redis key from request.client.host (rate_limit.py:20-21), and LoggingMiddleware logs the same value as "client" (middleware.py:29). In production the app runs behind Caddy via `reverse_proxy api:8000` (Caddyfile:19) and is started as plain `uvicorn ... --workers 1` with no --proxy-headers/--forwarded-allow-ips override (Dockerfile:73). uvicorn's default forwarded-allow-ips is 127.0.0.1, so it will NOT trust X-Forwarded-For coming from Caddy's docker-network IP, and the app never parses X-Forwarded-For itself. Consequently request.client.host is always Caddy's container IP for every external client.

**Impact:** All external clients share a single rate-limit bucket per key_prefix (e.g. community_photo_upload 30/hr, masjid_submission 10/hr, sslcommerz_ipn 120/min). One noisy or malicious client exhausts the shared bucket and 429s every other legitimate user (self-DoS), and abuse cannot be attributed to or blocked per source IP. Audit/request logs also record the proxy IP instead of the real client, undermining incident forensics on payment and upload surfaces.

**Recommendation:** Run uvicorn with --proxy-headers --forwarded-allow-ips set to the Caddy/container network (or place a trusted ProxyHeadersMiddleware) so request.client.host reflects the real client, or derive the limiter key from a validated X-Forwarded-For. Only trust the header from the known proxy.

<details><summary>Adversarial verification notes</summary>

The defect is real and reachable as described. app/core/rate_limit.py:20-21 builds the Redis rate-limit key from request.client.host, and app/core/middleware.py:29 logs the same value as "client". Neither parses X-Forwarded-For. A whole-repo grep for FORWARDED_ALLOW_IPS, proxy_headers, forwarded_allow, and X-Forwarded returns nothing, and main.py adds no ProxyHeadersMiddleware and no custom client-IP extraction. The production runtime CMD (Dockerfile:59, finding mis-cited as :73) is plain `uvicorn ... --workers 1` with no --proxy-headers/--forwarded-allow-ips. Uvicorn enables ProxyHeadersMiddleware by default but only trusts forwarded headers from forwarded_allow_ips, which defaults to 127.0.0.1; the .env.production.example sets no FORWARDED_ALLOW_IPS. In prod (docker-compose.prod.yml) the api service is only reachable via Caddy's `reverse_proxy api:8000` (Caddyfile:13, finding mis-cited as :19) over the docker bridge network, so Caddy's peer IP is a 172.x container address, not 127.0.0.1. Uvicorn therefore ignores Caddy's X-Forwarded-For and request.client.host resolves to Caddy's container IP for every external client. Consequently all 8 mounted rate limiters (sslcommerz_ipn 120/60s, community_photo_upload, masjid_submission, masjid_question, nearby, report, etc.) share one global bucket, enabling self-DoS by a single abusive client and defeating per-IP attribution, and request logs record the proxy IP rather than the true source. The two cited line numbers are off but the substance holds; this is not a misread.

</details>

---

### 10. 🟡 `MEDIUM` — .dockerignore excludes only .env, not .env.production, so real production secrets get baked into the migrate image built by docker-compose.prod.yml

**Dimension:** Deployment & Infrastructure · **Category:** `secret-in-image` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `.dockerignore:10`
- `Dockerfile:18`
- `Dockerfile:26`
- `docker-compose.prod.yml:106-111`
- `docker-compose.prod.yml:1-3`

**What's wrong:** .dockerignore lists `.env` but not `.env.production` (nor `.env.*`). The Dockerfile builder stage does `COPY . /app` (Dockerfile:18) and the `migrate` stage is `FROM builder` (Dockerfile:26), so it retains the full source tree. The documented prod workflow (docker-compose.prod.yml header, .env.production.example:3) is `docker compose -f docker-compose.prod.yml --env-file .env.production up -d`, and the migrate service builds from context `.` (docker-compose.prod.yml:107-110). At build time the operator's real `.env.production` (containing POSTGRES_PASSWORD, GOTRUE_JWT_SECRET, GOTRUE_SERVICE_ROLE_KEY, MINIO/AWS secrets, REDIS_PASSWORD, SSLCommerz store password, SMTP creds) sits in the build context and is copied into image masjidkoi-migrate:latest.

**Impact:** Production secrets are embedded in a container image layer. Anyone who can pull/read that image (registry push, image export, `docker history`, a shared host, backup of the image) recovers all live credentials, even though the file is git-ignored. The runtime api image is unaffected (it copies only /app/app and /app/.venv), but the migrate image is.

**Recommendation:** Add `.env` and `.env.*` (with `!*.example` exceptions if desired) to .dockerignore, or COPY only the needed subtree in the builder stage instead of `COPY . /app`.

<details><summary>Adversarial verification notes</summary>

Verified all cited files. .dockerignore:10 contains only the literal pattern `.env` (no `.env*`/`.env.*`), and Docker's filepath.Match semantics mean `.env.production` is NOT matched/excluded. Dockerfile:18 `COPY . /app` bakes the entire build context (the dependency layer uses non-persistent `--mount=type=bind`, but line 18 is a real COPY) into the builder image layer, including `.env.production` when present. Dockerfile:26 `FROM builder AS migrate` inherits the full `/app` tree and only adds dev deps, so the secret is retained in the migrate stage. docker-compose.prod.yml:106-111 builds the `migrate` service from context `.` with target `migrate`, tagged `masjidkoi-migrate:latest`; the documented workflow (compose header line 4, .env.production.example:3) is `--env-file .env.production up -d`, which requires `.env.production` to sit in the repo root = the build context at build time. The secret fields listed match .env.production.example exactly (POSTGRES_PASSWORD, GOTRUE_JWT_SECRET, GOTRUE_SERVICE_ROLE_KEY, AWS/MinIO keys, REDIS_PASSWORD, SSLCommerz store password, SMTP creds). I looked for guards to refute: no broader `.env*` dockerignore pattern, no discarding of the source tree in the migrate stage, and .gitignore is irrelevant to Docker builds. The finding correctly concedes the runtime `api` image is unaffected (Dockerfile:52-53 copy only /app/.venv and /app/app). The defect is real and reachable via the documented prod build path. Severity stays medium: exploitation needs read access to the image (docker history / export / registry / host / backup) rather than remote reachability, and on a single non-pushed host the exposure is contained, but it is a genuine secret-in-image defense-in-depth failure.

</details>

---

### 11. 🟡 `MEDIUM` — MinIO photos bucket is granted anonymous public-read in prod, but community/submission photos are written there in PENDING moderation status, so unmoderated content is publicly served the moment it is uploaded

**Dimension:** Deployment & Infrastructure · **Category:** `public-bucket-over-exposure` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** medium

**Locations:**
- `docker-compose.prod.yml:228-229`
- `app/services/community_photo_service.py:154-168`
- `app/services/masjid_submission_service.py:119-124`

**What's wrong:** minio-init runs `mc anonymous set download local/masjidkoi-photos` and `...masjidkoi-avatars` (docker-compose.prod.yml:228-229), making every object in those buckets anonymously downloadable. community_photo_service.upload writes the object to S3_BUCKET_PHOTOS and only then records the DB row with status=PhotoModerationStatus.PENDING (community_photo_service.py:154-168); masjid_submission_service likewise uploads submission photos to the same photos bucket. There is no pre-approval private staging bucket — moderation status lives only in the DB and gates the app feed, not S3 read access.

**Impact:** A photo that has not passed moderation (potentially abusive/objectionable imagery) is publicly retrievable via https://cdn.masjidkoi.me/masjidkoi-photos/community/<masjid_id>/<uuid>.<ext> the instant it is uploaded, regardless of PENDING/rejected state. If such a URL leaks (e.g. from the admin moderation view, logs, or the DB row that stores the public URL), the content is served to the public with no gate. Object keys use uuid4 so bulk enumeration is impractical, but per-object exposure and moderation bypass remain.

**Recommendation:** Stage unmoderated uploads in a private bucket (like masjidkoi-imports) and copy/move to the public photos bucket only on moderation approval, or serve via time-limited presigned URLs instead of a blanket anonymous-download policy.

<details><summary>Adversarial verification notes</summary>

All three cited locations and the supporting infra check out. docker-compose.prod.yml:228-229 runs `mc anonymous set download local/masjidkoi-photos` (and avatars), making every object anonymously downloadable in prod; the comment at line 230 confirms the intent. Caddyfile:23-27 reverse-proxies the CDN domain straight to minio:9000 with no auth or path gating, so the public-read policy is internet-reachable. community_photo_service.py:154-171 uploads the object to S3_BUCKET_PHOTOS FIRST, then writes the DB row with status=PENDING, and returns the deterministic public URL (line 160/181) to the uploader. masjid_submission_service.py:118-124 does the same for submission photos into the same bucket. Moderation is enforced only DB-side: list_public (community_photo_service.py:185+) filters via list_approved_community, which gates the app feed but has zero effect on raw S3/CDN read access. The repo's own e2e test corroborates the split: scripts/e2e_community_photos.py:118 notes the LOCAL bucket is private (403), while prod flips it public, and lines 121-125 assert the pending photo is absent only from the feed listing — precisely the DB-only gate that the public bucket bypasses. A PENDING/unmoderated photo is therefore publicly retrievable at its CDN URL the moment it is uploaded, and that URL is returned to the client. Could not refute: there is no private staging bucket, no object-level ACL, no presigned-URL scheme, and no Caddy path restriction anywhere. The only accurate caveat (already in the finding) is that uuid4 keys make bulk enumeration impractical, so this is per-object exposure / moderation bypass rather than mass scraping — which supports medium, not higher.

</details>

---

### 12. 🟡 `MEDIUM` — create_pending holds a DB transaction open across the up-to-15s SSLCommerz create_session HTTP call, pinning a PgBouncer server connection per in-flight checkout

**Dimension:** Payments & Financial Correctness · **Category:** `transaction-boundary` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/services/donation_service.py:204`
- `app/services/donation_service.py:209-219`
- `app/repositories/base.py:27-31`

**What's wrong:** In DonationService.create_pending, `await self.repo.add(donation)` calls `db.add` + `db.flush()` (base.py:27-31). The flush emits the PENDING INSERT and opens a SQLAlchemy/asyncpg transaction that stays open (no commit) until after the gateway call. The very next await is `self.gateway.create_session(...)`, an external HTTP POST to SSLCommerz with a 15s timeout (sslcommerz_gateway.py:37, 114-160). The commit only happens at line 236, after the network round-trip returns. So a live DB transaction — and, under PgBouncer transaction-pool mode, one of the pool's server connections — is held for the entire duration of the gateway HTTP call on every checkout-init request. This is the exact anti-pattern that complete_from_ipn explicitly documents and avoids: at donation_service.py:290-297 the IPN path calls `await self.db.commit()` to release its PgBouncer server connection BEFORE the identical up-to-15s validate_ipn call, precisely because 'Holding it open across up to 15s of gateway I/O would pin one of the pool's 20 server connections per in-flight IPN and starve the rest of the app under a burst.' The checkout path violates that same rule. Note the flush is currently load-bearing only because Donation.donation_id uses a Python-side `default=uuid.uuid4` (donation.py:74-76) applied at flush time; the id could be generated in Python (or the PENDING row committed) before the gateway call instead.

**Impact:** Under a burst of concurrent checkout-init requests (e.g. a campaign push driving many donors to tap 'Donate' at once, or the SSLCommerz endpoint responding slowly), each in-flight request pins a PgBouncer server connection for up to 15s while doing zero DB work. With the documented ~20 server connections, ~20 simultaneous slow checkouts exhaust the pool and every other request in the app (prayer times, feeds, even IPN completion) blocks or times out waiting for a connection — a self-inflicted denial of service triggered exactly when donation volume is highest.

**Recommendation:** Release the DB transaction before the gateway call, mirroring complete_from_ipn: either generate donation_id in Python and commit the PENDING row (or at minimum `await self.db.commit()` after the flush) before calling create_session, then re-load/lock the row to write gateway_session_key and commit again; or restructure so create_session runs with no open transaction. This keeps the PgBouncer connection free during the external I/O.

<details><summary>Adversarial verification notes</summary>

Every cited fact checks out in the source. base.py:27-31 `add()` does `db.add()` + `await db.flush()` with no commit (docstring: "flush ... no commit"), so donation_service.py:204 emits the PENDING INSERT and opens a server-side transaction. The next await (209-219) is `gateway.create_session(...)`, an external SSLCommerz HTTP POST bounded by `_TIMEOUT = httpx.Timeout(15.0)` (sslcommerz_gateway.py:37, POST at 158-160). No commit occurs between the flush (204) and the gateway call; the first commit is at line 236 (or 223 on failure), after the network round-trip returns. Under PgBouncer transaction-pool mode a server connection is pinned to the client for the full duration of an open transaction, so one of the pool's server connections is held for up to 15s per in-flight checkout while doing zero DB work. This is provably the same anti-pattern the IPN path documents and deliberately avoids: donation_service.py:290-297 commits to release its PgBouncer server connection BEFORE the identical up-to-15s validate_ipn call, with a comment explaining that holding it open would "pin one of the pool's 20 server connections per in-flight IPN and starve the rest of the app under a burst." The checkout path violates that exact rule. The finding's own caveat — that the flush is only load-bearing because donation_id uses a Python-side uuid4 default applied at flush time, and the id/row could be produced before the gateway call — is accurate and shows the pinning is avoidable, not a reason to dismiss it. I could not refute it: no earlier commit, no connection-release, and no framework mechanism returns the connection mid-transaction.

</details>

---

### 13. 🟡 `MEDIUM` — Route executes raw SQL directly against the database, bypassing repository and service layers

**Dimension:** Separation of Concerns · **Category:** `layer-violation` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/routers/auth.py:245-268`

**What's wrong:** The `list_factors` route imports `AsyncSession` (auth.py:23), injects `db: AsyncSession = Depends(get_db)` (line 245), and runs a raw `db.execute(sql_text("SELECT id, status, friendly_name FROM auth.mfa_factors ..."))` (lines 249-257) directly in the HTTP handler. This violates the mandated router -> service -> repository layering: routers must never import AsyncSession or issue DB queries. There is no repository or service for this query at all. It also returns a raw `dict` with no `response_model`/Pydantic schema (return type `-> dict`, lines 259-268).

**Impact:** A schema change to auth.mfa_factors or a query bug is invisible to the model/migration layer and untestable at the repository level; the handler couples the HTTP layer to the physical DB schema. The un-schema'd dict response bypasses response validation, so a shape change silently leaks to clients.

**Recommendation:** Move the query into a repository method (e.g. MfaFactorRepository.list_verified_totp(user_id)), call it via a service, and return a Pydantic response model set as `response_model` on the route.

<details><summary>Adversarial verification notes</summary>

Every claim in the finding is verifiable in the source. app/routers/auth.py:23 imports `from sqlalchemy.ext.asyncio import AsyncSession`. The `list_factors` handler (lines 243-268) injects `db: AsyncSession = Depends(get_db)` (line 245) and executes raw SQL in the HTTP handler via `await db.execute(sql_text("SELECT id, status, friendly_name FROM auth.mfa_factors WHERE user_id = :uid AND factor_type = 'totp' AND status = 'verified' ORDER BY created_at"), {"uid": str(user.user_id)})` (lines 249-257). The return type is `-> dict` (line 246) and it returns a hand-built raw dict (lines 259-268) with no `response_model` on the decorator (lines 237-242) and no Pydantic schema — confirming the un-schema'd response claim. This directly violates the CLAUDE.md rules "Routes must never import AsyncSession or query the DB directly" and "Never return a raw ORM model / always a Pydantic schema at the boundary." I confirmed no factors repository or service exists (grep of app/repositories/ and app/services/ shows only gotrue_client.py referencing factors via HTTP, and otp_auth_service.py — none query auth.mfa_factors), so the query bypasses the layering entirely with no repo/service to test at. It is not a mere hypothetical: the route is a mounted GET /2fa/factors endpoint reachable by any authenticated user.\n\nSeverity adjustment: I downgrade from high to medium. The violation is real and clear-cut, but the concrete consequences are maintainability/testability, not correctness or security. The SQL is parameterized (`:uid` bind param, no injection), reads only the caller's own factors (scoped by authenticated user.user_id), and auth.mfa_factors is a GoTrue-owned table that legitimately lives outside the app's own models/migrations, so the "invisible to migration layer" impact is inherent to any query against GoTrue's schema rather than a fixable modeling gap. No runtime fault or exploit is reachable. That places it at medium for a layer/separation-of-concerns defect.

</details>

---

### 14. 🟡 `MEDIUM` — Admin routes instantiate repositories directly and call them from the HTTP layer, bypassing services

**Dimension:** Separation of Concerns · **Category:** `layer-violation` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/routers/admin.py:61-64`
- `app/routers/admin.py:85-89`
- `app/routers/admin.py:230-231`

**What's wrong:** Several admin routes inject `db: AsyncSession` and construct repositories inline in the handler: `get_stats` builds `UserProfileRepository(db)` and `MasjidCampaignRepository(db)` and calls `count_non_deleted()`/`get_active_count()` (lines 61-64); `get_audit_log` builds `AuditLogRepository(db)` and calls `get_paginated()` (lines 85-89); `user_growth` builds `UserProfileRepository(db)` and calls `get_growth()` (lines 230-231). Per CLAUDE.md rule 1/2, routers are HTTP-only and must delegate to a service; they must not import AsyncSession or touch repositories. `get_audit_log` additionally performs entity->schema mapping (business/transform logic) inline (lines 91-104).

**Impact:** Business orchestration (composing multiple repositories for the stats/growth payloads) lives in the HTTP layer where it cannot be reused or unit-tested at the service boundary; the layering contract is broken for every one of these endpoints.

**Recommendation:** Add service methods (e.g. AdminUserService.get_growth, an admin stats service) that own repository composition and mapping; inject the service via a dependency factory and remove the AsyncSession/repository imports from the router.

<details><summary>Adversarial verification notes</summary>

Read app/routers/admin.py directly. Line 9 imports AsyncSession into the router module (violates CLAUDE.md rule 1). Lines 20-22 import AuditLogRepository, MasjidCampaignRepository, UserProfileRepository into the HTTP layer. get_stats (lines 57-64) injects db: AsyncSession = Depends(get_db) and constructs profile_repo = UserProfileRepository(db) and campaign_repo = MasjidCampaignRepository(db), then calls count_non_deleted() and get_active_count() directly from the handler (it also reaches into ann_service.repo.get_counts() at line 60, another layering leak). get_audit_log (lines 83-108) constructs AuditLogRepository(db), calls get_paginated(), and does the entity->AuditLogEntry mapping inline. user_growth (lines 228-231) constructs UserProfileRepository(db) and calls get_growth(period) directly. All cited line ranges, class names, and method names match the finding exactly. This directly breaks the strict router->service->repository layering mandated by CLAUDE.md rules 1 and 2; the correctly-layered endpoints in the same file (e.g. get_settings at 246-251 using a service dependency factory) confirm the intended pattern is being bypassed here. No guard or service delegation exists for these three handlers.

</details>

---

### 15. 🟡 `MEDIUM` — Route commits the session and writes audit log directly instead of the service layer

**Dimension:** Separation of Concerns · **Category:** `layer-violation` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/routers/admin.py:286-294`

**What's wrong:** The `broadcast_push` route injects `db: AsyncSession` (line 280), instantiates `AuditLogRepository(db)` and calls `.log(...)` (lines 286-293), then calls `await db.commit()` (line 294) directly in the HTTP handler. CLAUDE.md rule 4 requires explicit commits to happen in the service layer, and rule 1/2 forbid routers from touching the session or repositories.

**Impact:** Transaction-boundary control leaks into the router; the audit-write-plus-commit is not encapsulated in a service, so it cannot be reused (e.g. by other broadcast paths) and there is no single service owning the commit for this operation.

**Recommendation:** Move the audit logging and commit into PushService.broadcast_platform_push (or a dedicated admin service method) and have the route simply return the result.

<details><summary>Adversarial verification notes</summary>

The finding is accurate and grounded in the actual source. In app/routers/admin.py the `broadcast_push` handler: (1) imports AsyncSession (line 9) and injects `db: AsyncSession = Depends(get_db)` (line 280); (2) directly instantiates a repository and writes with `await AuditLogRepository(db).log(...)` (lines 286-293); and (3) commits the transaction itself with `await db.commit()` (line 294). This violates CLAUDE.md rule 1 (routers must not import/use AsyncSession directly), rule 2 (Repository -> Service -> Route layering — routers must not instantiate repositories), and rule 4 (explicit commit belongs in the service layer). The business action IS delegated to PushService (line 284), but the audit-write-plus-commit is done inline in the HTTP handler with no service encapsulating the transaction boundary. No guard elsewhere makes this compliant; the violation executes on every successful call. I could not refute it — the code does exactly what the finding claims. However, this is a pure layering/maintainability defect with no correctness, security, or runtime consequence (the commit succeeds and the audit log is correctly written), so the claimed severity of high is overstated; medium is appropriate.

</details>

---

### 16. 🟡 `MEDIUM` — Route performs external HTTP orchestration to GoTrue and returns an unvalidated dict

**Dimension:** Separation of Concerns · **Category:** `layer-violation` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/routers/admin.py:133-156`

**What's wrong:** The `list_admin_users` route opens `httpx.AsyncClient` and calls the GoTrue admin API directly (lines 133-140), then transforms the response and builds the payload inline (lines 144-156). This external-service orchestration and mapping is business logic that belongs in a service (e.g. AdminUserService), not the HTTP layer. The route also has no `response_model` and returns a raw `dict` (return type `-> dict`, line 132; returns at lines 142 and 156).

**Impact:** GoTrue coupling and response shaping are stuck in the router, untestable behind a service seam and unreusable; the un-schema'd dict bypasses response validation so shape drift leaks to clients silently.

**Recommendation:** Move the GoTrue call and mapping into AdminUserService, return a Pydantic response model, and set it as `response_model` on the route.

<details><summary>Adversarial verification notes</summary>

The finding is directly verifiable in app/routers/admin.py:130-156. The `list_admin_users` route handler (a) constructs its own `httpx.AsyncClient` and calls the GoTrue admin API `/admin/users` inline (lines 133-140), (b) hand-rolls the auth headers with `GOTRUE_SERVICE_ROLE_KEY` (137-138), (c) applies ad-hoc error handling returning `{"users": []}` on failure (141-142), and (d) maps the GoTrue payload into a client shape inline (144-156). It is declared `-> dict` (line 132) with no `response_model` on the decorator (lines 126-129), so it returns a raw unvalidated dict at lines 142 and 156.

This is a real, non-hypothetical violation of the documented layering rules, and the seams it bypasses already exist in this exact codebase: the same file imports `AdminUserService` (line 39) and `get_admin_user_service` (line 14) and uses them for the sibling `list_app_users` route (line 172). More damningly, there is a dedicated `GoTrueClient` singleton (`app/services/gotrue_client.py`) that already centralizes GoTrue admin-API access with shared `_admin_headers()` (line 26) and `_raise_for_gotrue()` (line 34) helpers and hits `/admin/users` in numerous methods (create_admin_user, ban_user, delete_user, update_user_app_metadata, etc.). So the route both (1) places external-service orchestration + response shaping in the HTTP layer instead of a service, violating CLAUDE.md rule #2 (router->service->repository) and #5 (route = HTTP only), and (2) returns a raw dict with no Pydantic response_model, violating rule #6 (schema validation at the boundary) — meaning field/shape drift from GoTrue leaks to clients with no validation. The httpx call also duplicates header/error logic the existing GoTrueClient already provides.

The route is reachable: it is registered on the admin router (prefix "/admin", line 45) as GET /admin/users, gated only by require_platform_admin auth, so a platform admin hits this code path directly. Nothing elsewhere mitigates the layer/schema violation.

</details>

---

### 17. 🔵 `LOW` — Co-admins have full masjid_admin parity — any co-admin can revoke the inviting admin and invite further admins

**Dimension:** Authentication & Authorization · **Category:** `privilege-model` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** medium

**Locations:**
- `app/services/co_admin_invite_service.py:32-71`
- `app/services/co_admin_invite_service.py:171-195`

**What's wrong:** invite() always mints the invitee with role=AdminRole.MASJID_ADMIN for the same masjid (co_admin_invite_service.py:45-50); there is no owner-vs-co-admin distinction. revoke() only enforces _check_scope (same masjid) and then get_active_by_gotrue_user_masjid + gotrue.delete_user, with no check that the caller is not targeting the primary/inviting admin. Because every co-admin holds identical MASJID_ADMIN authority, any co-admin can revoke (delete the GoTrue account of) any other active admin of that masjid — including the admin who invited them — and can invite additional co-admins.

**Impact:** A single co-admin invited for a masjid can lock out or remove the original masjid owner and seize sole control of the masjid's admin surface (announcements, prayer times, campaigns, donation dashboards). Horizontal privilege abuse within a masjid with no safeguard on who may revoke whom.

**Recommendation:** Introduce an owner/primary-admin distinction (or a 'cannot revoke the inviter / cannot revoke self-elevated' rule): restrict co-admin invite and revoke to the primary admin (or platform_admin), or at minimum forbid revoking the account that created the invite.

<details><summary>Adversarial verification notes</summary>

The core privilege-model defect is real and reachable. invite() (co_admin_invite_service.py:45-50) always mints the invitee as AdminRole.MASJID_ADMIN scoped to the same masjid with no owner/co-admin tier. create_admin_user (gotrue_client.py:184-186) writes role=masjid_admin + masjid_id into app_metadata, so once a co-admin accepts (accept() logs them in), their JWT carries role=masjid_admin and passes require_masjid_admin (auth.py:94) and _check_scope (co_admin_invite_service.py:201). revoke() (lines 171-195) enforces only _check_scope and then deletes whatever active invite record it finds via get_active_by_gotrue_user_masjid, with no guard preventing a co-admin from targeting the co-admin who invited them — so co-admin B invited by co-admin A can delete A's GoTrue account, and any co-admin can chain further invites. That is a genuine horizontal privilege abuse with no safeguard on who may revoke whom.

However, the finding's headline impact is overstated. revoke() resolves its target through the MasjidCoAdminInvite table (repository lines 24-34, status IN Pending/Accepted). The original/primary masjid owner is provisioned via POST /admin/invite by a platform_admin (auth.py:297-312), which calls only gotrue.create_admin_user and writes NO MasjidCoAdminInvite row. Therefore revoke() against that owner's gotrue_user_id returns None -> 404: a co-admin CANNOT remove the platform-provisioned original owner or "seize sole control" from them. The revoke risk is confined to co-admins deleting each other (including a co-admin inviter). Because the underlying defect (flat masjid_admin parity, no owner distinction, mutual revoke + invite chaining among co-admins) is confirmed and reachable, I confirm the finding but note the impact should be scoped to co-admin-vs-co-admin, not owner takeover. Severity low is appropriate: the abuse is intra-masjid, requires a trusted accepted co-admin, and cannot oust the true owner.

</details>

---

### 18. 🔵 `LOW` — list_goals issues an N+1 query — one progress query per goal

**Dimension:** General Correctness · **Category:** `n-plus-one` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** medium

**Locations:**
- `app/services/goal_service.py:90`
- `app/services/goal_service.py:184`
- `app/services/goal_service.py:194`

**What's wrong:** list_goals builds each item via `[await self._build_response(g, user) for g in goals]`. _build_response runs one extra DB round-trip per goal: sum_quran_amount() for quran_quantity goals or completion_dates() for recurring goals. So listing N goals executes 1 + N queries sequentially on the request-scoped session. This contradicts the repo's own N+1-avoidance pattern used elsewhere (e.g. MasjidRepository.names_by_ids, DonationService.all_balances).

**Impact:** A user with many active goals triggers a query per goal on every list call. Correct results, but latency scales linearly with goal count and adds avoidable load; on a large goal list this is a noticeable per-request cost.

**Recommendation:** Batch the progress inputs: fetch all completion_dates for the user's recurring goals in one grouped query and all journal Qur'an sums per (unit, window) in one query, then compute progress in memory. Alternatively cap/paginate the goal list.

<details><summary>Adversarial verification notes</summary>

Verified in app/services/goal_service.py. list_goals (line 90) does `items = [await self._build_response(g, user) for g in goals]` — one sequential await per goal. _build_response (line 181) issues exactly one extra query per goal depending on kind: journal_repo.sum_quran_amount() (line 184) for QURAN_QUANTITY goals, or repo.completion_dates(goal.goal_id) (line 194) for recurring goals. Confirmed in the repositories: user_journal_repository.py:39-59 (sum_quran_amount runs a single aggregate query scoped to one goal's unit/date window) and user_goal_repository.py:38-44 (completion_dates runs a single SELECT scoped to one goal_id). Neither accepts a batch of goal IDs, and list_for_user (user_goal_repository.py:25-34) returns bare UserGoal rows with no eager-loaded completions/sums. So listing N goals executes 1 + N queries serially on the request-scoped session — a genuine N+1 with no guard elsewhere. Results are correct; only latency/load scale with goal count.

</details>

---

### 19. 🔵 `LOW` — Donation history keyset pagination emits a next_cursor for the final full page (phantom empty page)

**Dimension:** General Correctness · **Category:** `pagination` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** medium

**Locations:**
- `app/services/donation_service.py:674`
- `app/repositories/donation_repository.py:156`

**What's wrong:** get_history sets `next_cursor = items[-1].created_at if len(items) == limit else None`. list_for_user fetches exactly `limit` rows (not limit+1), so when the total number of matching donations is an exact multiple of limit, the last real page returns `limit` rows and a non-null next_cursor even though no further rows exist. The client then makes one more request that returns an empty page. Distinct from the correct limit+1 keyset pattern the feed repository uses (feed_repository.py fetches limit+1 to detect a further page).

**Impact:** No data loss or duplication, but the client is told there is another page when there isn't, causing a wasted round-trip and a possible empty-state flash at the end of donation history whenever the count is a multiple of the page size.

**Recommendation:** Fetch limit+1 rows in list_for_user, return at most `limit` items, and set next_cursor only when the extra row was present (mirroring FeedRepository.announcements_feed).

<details><summary>Adversarial verification notes</summary>

Read both cited locations. donation_repository.py:186-193 (list_for_user) fetches exactly .limit(limit) rows — no limit+1 over-fetch, no count check — and returns them. donation_service.py:674 sets next_cursor = items[-1].created_at if len(items) == limit else None, deriving "has next page" solely from the page being full. Therefore when the total matching donations is an exact multiple of limit, the last real page returns limit rows and a non-null next_cursor; a follow-up request with cursor=that created_at applies WHERE created_at < before (repository line 184-185) and returns an empty page. This is the classic phantom-page bug of a keyset paginator that does not over-fetch by one. The finding's supporting contrast is also verified: feed_repository.py:49 and :108 use .limit(limit + 1) precisely to detect a further page, confirming the donation path deviates from the repo's own correct pattern. Impact matches: no data loss or duplication, just a wasted round-trip and possible empty-state flash whenever the count is a multiple of page size. Low severity is correct — it is a UX/efficiency nit with no correctness-of-data consequence.

</details>

---

### 20. 🔵 `LOW` — Unauthenticated /health endpoint leaks raw database exception strings

**Dimension:** Data Exposure & Web Security · **Category:** `info-disclosure` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/main.py:207`
- `app/main.py:225`

**What's wrong:** The public, unauthenticated /health handler catches any DB exception and returns str(exc) verbatim in the JSON response body under checks.error (main.py:207 assigns db_error = str(exc); main.py:225 splices it into the response). asyncpg / SQLAlchemy connection errors commonly include internal hostnames, ports, PgBouncer/Postgres DSN fragments, and driver internals.

**Impact:** When the DB or PgBouncer is degraded, any anonymous caller of GET /health receives internal infrastructure detail (e.g. host:port of the internal database, connection-pool internals) that aids reconnaissance of the private network topology.

**Recommendation:** Return a generic status ("database":"error") to clients and log the detailed str(exc) server-side only; do not include db_error in the HTTP response body.

<details><summary>Adversarial verification notes</summary>

The /health handler at app/main.py:191-228 is registered with @app.get("/health") and has no authentication dependency; the app only mounts CORSMiddleware (main.py:138) and LoggingMiddleware (main.py:151), so it is reachable by any anonymous HTTP client. On a DB exception the handler assigns db_error = str(exc) verbatim (main.py:207) and splices it into the response body under checks.error (main.py:225: **({\"error\": db_error} if db_error else {})), returned with HTTP 503. There is no environment gating — settings.APP_ENV is only echoed, never used to redact the error, so production leaks it too. asyncpg/SQLAlchemy connection errors routinely embed the DSN host and port (e.g. connection to server at \"pgbouncer\" ... port 6432 failed), which aids reconnaissance of the private network. The defect is real and reachable exactly as described.

</details>

---

### 21. 🔵 `LOW` — CORS allows localhost dev origins with credentials in the single production config

**Dimension:** Data Exposure & Web Security · **Category:** `cors-config` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** medium

**Locations:**
- `app/main.py:140`
- `app/main.py:146`

**What's wrong:** The CORS middleware hardcodes http://localhost:3000, http://localhost:3001 and http://127.0.0.1:3000 alongside the real https://admin.masjidkoi.me, with allow_credentials=True, allow_methods=["*"], allow_headers=["*"] (main.py:140-149). There is no environment switch, so the localhost origins remain trusted in production. Origins are an explicit list (not a wildcard) so this is not a wide-open CORS-with-credentials hole, but any page served from localhost:3000/3001 on a victim's machine can make credentialed cross-origin requests against the production API.

**Impact:** A locally-running malicious/dev process (or a tool that binds localhost:3000) on an authenticated user's machine can issue credentialed requests to the production API with the browser attaching cookies/authorization, since those origins are permanently whitelisted with credentials.

**Recommendation:** Drive allow_origins from settings/APP_ENV so localhost origins are only permitted in non-production, and keep production restricted to https://admin.masjidkoi.me.

<details><summary>Adversarial verification notes</summary>

The code at app/main.py:138-149 is exactly as described: a single, unconditional CORSMiddleware registration with allow_origins hardcoding http://localhost:3000, http://localhost:3001, http://127.0.0.1:3000 plus https://admin.masjidkoi.me, and allow_credentials=True, allow_methods=["*"], allow_headers=["*"]. A repo-wide grep for CORS/allow_origins/ALLOWED_ORIGINS found only this one block — there is no settings-driven or environment-gated variant that removes the dev origins in production. So the factual core of the finding holds: the localhost dev origins remain a credentialed, trusted CORS origin in the production configuration.\n\nI could not refute it. The only mitigating nuance (which the finding itself already concedes) is that this is an explicit allowlist, not a reflected/wildcard origin, so it is not a wide-open credentialed-CORS hole, and exploitation requires an attacker to already control a page served from localhost:3000/3001 on the victim's authenticated machine (local process/tool binding that port). That is a meaningful precondition and keeps real-world impact modest, but the misconfiguration is real and the described attack path (browser attaches the production API's credentials to a cross-origin request from a localhost-served page, and CORS lets that page read the response) is reachable. This is a legitimate defense-in-depth / hygiene defect, correctly rated low.

</details>

---

### 22. 🔵 `LOW` — Dev frontend is built to call the API on port 8001, but the API is published on 8000 — no service listens on 8001

**Dimension:** Deployment & Infrastructure · **Category:** `port-mismatch` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `docker-compose.yml:165`
- `docker-compose.yml:7-8`
- `.env.example:10`

**What's wrong:** The dev frontend build arg defaults NEXT_PUBLIC_API_URL to http://localhost:8001 (docker-compose.yml:165, matching .env.example:10), but the api service publishes 8000:8000 (docker-compose.yml:7-8) and nothing in the dev compose maps host port 8001.

**Impact:** A developer following .env.example gets a frontend bundle that points at http://localhost:8001, which has no listener, so browser API calls fail with connection refused until the value is manually corrected to 8000.

**Recommendation:** Set NEXT_PUBLIC_API_URL default to http://localhost:8000 in docker-compose.yml and .env.example, or publish the api on 8001 to match.

<details><summary>Adversarial verification notes</summary>

Every cited fact holds. docker-compose.yml:165 sets `NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-http://localhost:8001}`; .env.example:10 sets `NEXT_PUBLIC_API_URL=http://localhost:8001`; the api service publishes only `8000:8000` (docker-compose.yml:7-8). I enumerated every dev service's port mappings (postgres 5432, gotrue 9999, frontend 3000, minio 9090/9091, redis none) and confirmed nothing maps host port 8001. I also checked the actual .env — it does NOT define NEXT_PUBLIC_API_URL, so the compose fallback (8001) is exactly what a developer gets. Since NEXT_PUBLIC_* is baked into the Next.js bundle at build time and used by the browser on the host, the client resolves localhost:8001 and gets connection refused. Production is not a false-positive escape hatch: docker-compose.prod.yml:289 overrides NEXT_PUBLIC_API_URL to https://${API_DOMAIN} with Caddy proxying to api:8000, so the bug is confined to local dev but is real and reachable there. Not a misread, no guard exists elsewhere for the dev path.

</details>

---

### 23. 🔵 `LOW` — No healthcheck on the api service; Caddy (prod) and frontend depend on it by start-order only, so the reverse proxy can begin routing before uvicorn is ready

**Dimension:** Deployment & Infrastructure · **Category:** `startup-race` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** medium

**Locations:**
- `docker-compose.prod.yml:251-281`
- `docker-compose.prod.yml:49-52`
- `docker-compose.yml:1-25`

**What's wrong:** The api service defines no healthcheck in either compose file, even though a proper /health endpoint exists (app/main.py). In prod, caddy uses plain `depends_on: [api, gotrue, minio]` (docker-compose.prod.yml:49-52) which waits only for container start, not readiness; the api itself waits on its dependencies' health but nothing waits on the api's.

**Impact:** On cold start Caddy may proxy client/browser requests to api:8000 before uvicorn has bound and completed lifespan startup (Redis pool, scheduler), returning 502s during the window. Self-healing, but a visible startup blip and it prevents other services from using `condition: service_healthy` against the API.

**Recommendation:** Add a healthcheck to the api service (e.g. curl -f http://localhost:8000/health) and gate caddy/frontend on `condition: service_healthy`.

<details><summary>Adversarial verification notes</summary>

Every cited fact checks out. docker-compose.prod.yml:251-281 defines the `api` service with build/image/env_file/expose/depends_on/security_opt/deploy but NO `healthcheck` block; Dockerfile:1-60 has no `HEALTHCHECK` instruction either, so no health signal exists for the API. docker-compose.prod.yml:49-52 shows `caddy` using the list-form `depends_on: [api, gotrue, minio]`, which waits only for container start (running state), not readiness; frontend (297-298) does the same. Caddyfile:13 does a plain `reverse_proxy api:8000` with no retry/health directives, so a dial to an unbound port returns 502 without retry. The race is reachable on cold start: api's own depends_on conditions gate it on healthy dependencies, but nothing gates caddy on the api's readiness, so caddy can begin routing before uvicorn binds and finishes lifespan startup (Redis pool + APScheduler), yielding transient 502s. The gap also blocks any `condition: service_healthy` dependency on the API. It is genuine but self-healing (restart: unless-stopped, tiny cold-start window only), so low severity is correct.

</details>

---

### 24. ⚪ `INFO` — refund() holds a donation row under SELECT ... FOR UPDATE across the external SSLCommerz gateway HTTP call (up to 15s), pinning a PgBouncer transaction-mode server connection for the duration

**Dimension:** Concurrency & Deadlocks · **Category:** `long-transaction-across-io` · **Verifier verdict:** CONFIRMED · **Reporter confidence:** high

**Locations:**
- `app/services/donation_service.py:479`
- `app/services/donation_service.py:491`
- `app/services/sslcommerz_gateway.py:37`

**What's wrong:** In `refund()` the row is locked with `get_for_update()` (line 479) and the transaction is held open across `self.gateway.refund(...)` (line 491), an httpx call with a 15s timeout (sslcommerz_gateway.py:37). Under PgBouncer transaction-pool mode + NullPool, an open transaction pins one of the finite server-side PostgreSQL connections for the full network round-trip. This is explicitly acknowledged in the docstring (lines 469-475) as an accepted trade-off because refunds are rare and admin-only, and it is the correct way to serialise concurrent refunds — so it is not a bug, but it is the one place the codebase holds a DB lock across external I/O.

**Impact:** If many concurrent refunds ever occurred (or the gateway hangs near the 15s timeout), each in-flight refund pins a server connection for up to 15s, which could contend for the pool. Bounded by refund being a rare admin action.

**Recommendation:** No change required given the rarity; if refund volume ever grows, move to the IPN pattern (commit the read, call the gateway unlocked, then re-lock briefly to flip status) or use an advisory/optimistic guard instead of holding the row lock across the HTTP call.

<details><summary>Adversarial verification notes</summary>

The factual mechanism is exactly as described. donation_service.py:479 acquires the row via repo.get_for_update() (SELECT ... FOR UPDATE), opening the transaction. The gateway HTTP call at line 491 (self.gateway.refund(...)) is awaited before any commit — commit is not reached until line 529. sslcommerz_gateway.py:37 sets _TIMEOUT = httpx.Timeout(15.0), and refund() (lines 289-292) issues the GET under that timeout. Under PgBouncer transaction-pool mode with NullPool, an open transaction does pin a server-side connection until commit/rollback, so the connection is held for the full network round-trip. The path is reachable: a COMPLETED SSLCommerz donation carries gateway_bank_tran_id, so the line 490 guard passes and the gateway is called. However, this is a deliberate, documented design decision, not a bug: the docstring (lines 469-475) explicitly names it as an accepted trade-off to serialise concurrent admin refunds, contrasting it against the IPN hot path, and the finding itself states 'it is not a bug' and 'it is the correct way to serialise concurrent refunds.' Impact is bounded — refunds are rare, admin-only, and the gateway call is conditional. So the described behavior is confirmed as present, but it is an intentional, correct-by-design trade-off rather than an actionable defect; the appropriate severity is info.

</details>

---

### 25. ⚪ `INFO` — Recurring-donation nudge sweep reads due rows without a row lock, unlike the stale-pending sweep, so it relies solely on the fail-open Redis lock to avoid duplicate nudges/double-advance

**Dimension:** Concurrency & Deadlocks · **Category:** `missing-row-lock` · **Verifier verdict:** PLAUSIBLE · **Reporter confidence:** medium

**Locations:**
- `app/services/recurring_schedule_service.py:210`
- `app/services/recurring_schedule_service.py:224`
- `app/repositories/donation_repository.py:127`

**What's wrong:** `run_due_nudges` calls `self.repo.due(now)` (line 210) and then advances `next_due_at` / cancels and commits (line 224) with no `FOR UPDATE`/`SKIP LOCKED` on the selected rows. This is asymmetric with the stale-pending sweep, which deliberately locks its rows with `.with_for_update(skip_locked=True)` (donation_repository.py:127) precisely so concurrent runners can't clobber each other. The nudge path's only guard against a concurrent second runner is the `_run_singleton` Redis lock, which fails OPEN when Redis is unavailable (scheduler.py:33-37).

**Impact:** If two scheduler instances run this job concurrently (Redis down → lock fails open, or a scaled deployment during a TTL-overrun window), both read the same due schedules, both send a RECURRING_NUDGE push, and both advance next_due_at — duplicate nudges and a potential double-advance skipping a cycle. No impact in the current single-worker deployment.

**Recommendation:** Lock the due rows with `FOR UPDATE SKIP LOCKED` and advance+commit within the same locked transaction (mirroring `list_stale_pending`), so correctness does not depend on the fail-open Redis lock.

<details><summary>Adversarial verification notes</summary>

The code facts are all accurate: recurring_schedule_service.py:210 calls repo.due(now), whose query (recurring_schedule_repository.py:33-44) has no FOR UPDATE/SKIP LOCKED; the service advances next_due_at/cancels and commits at line 217-224 unlocked; the stale-pending sweep by contrast locks with .with_for_update(skip_locked=True) (donation_repository.py:127); and _run_singleton fails OPEN when Redis is down (scheduler.py:31-37). However, the DESCRIBED impact (duplicate nudges / double-advance) requires two sweep runs executing concurrently, and every path to that is closed in the actual system: prod runs uvicorn --workers 1 (Dockerfile:59) in a single non-replicated container explicitly commented "single worker so the APScheduler runs exactly once" (docker-compose.prod.yml:262); SCHEDULER_ENABLED gates the scheduler to one instance (main.py:56-59); and no max_instances override on add_job means APScheduler's default max_instances=1 prevents intra-process overlap of the same job even on a TTL-overrun tick. The Redis-fail-open path alone cannot cause duplicates with a single runner. Additionally the asymmetry is partly justified: the stale-pending lock defends against a real same-deployment concurrent writer (the SSLCommerz IPN mutating the same donation row in the request path), which has no analogue for the recurring-schedule sweep. The finding itself concedes no impact in the current single-worker deployment. So this is a genuine but latent defense-in-depth gap that only manifests on a hypothetical future multi-replica deployment, not a defect reachable in the code/deployment as it stands.

</details>

---

## Unverified leads (completeness critic)

> These were surfaced by a final critic pass and were **NOT independently verified**. Treat as leads to confirm, not confirmed defects.

### C1. 🟠 `HIGH` — Passwordless email-OTP login is not scoped to consumer accounts — it is a password-free, MFA-free login path for admin accounts

**Category:** `auth-escalation`

**Locations:**
- `app/routers/auth.py:104`
- `app/services/otp_auth_service.py:74`
- `app/services/otp_auth_service.py:109`
- `app/services/gotrue_client.py:99`
- `app/dependencies/auth.py:68`

**What's wrong:** The consumer email-OTP endpoints /auth/otp/request and /auth/otp/verify do nothing to restrict which accounts may use them. request_otp() calls gotrue.ensure_consumer_user(email), which on an already-existing account returns early WITHOUT touching its role (gotrue_client.py:122-123), then gotrue.send_email_otp() emails a 6-digit code to whatever account owns that email — including platform_admin / masjid_admin accounts. verify_otp() (otp_auth_service.py:109) accepts the code and returns the GoTrue session verbatim; that session's JWT still carries the account's app_metadata.role (e.g. platform_admin) at aal=aal1. Nothing in the OTP path checks that the account's role is app_user. This is a distinct root cause from the already-noted OTP lockout/cap bypass: the defect here is the missing account-type gate, which turns a consumer login into a full-privilege admin login that never touches the password. Because platform-admin aal2/TOTP enforcement is commented out (dependencies/auth.py:79-82), the resulting aal1 admin token has full access.

**Impact:** Anyone who can read an admin's email inbox (or who guesses the emailed 6-digit code — trivial once the already-reported Redis-outage lockout/throttle bypass is in play) obtains a full platform_admin JWT via /auth/otp/verify, completely bypassing the admin password AND the intended TOTP second factor. Admin account takeover through a flow meant only for donor sign-in.

**Recommendation:** In verify_otp (or ensure_consumer_user/send flow) reject or refuse to mint sessions for any account whose app_metadata.role is not app_user; alternatively run admins on a separate GoTrue instance/audience, or gate the OTP endpoints to non-admin emails. At minimum, re-enable the aal2 requirement on privileged endpoints so an aal1 OTP session cannot reach admin operations.

---

### C2. 🟠 `HIGH` — Admin re-invite looks up existing users on only the first page of GoTrue's user list, so it breaks once the user table exceeds the page size — and consumer OTP auto-provisioning inflates that table

**Category:** `correctness-scaling`

**Locations:**
- `app/services/gotrue_client.py:238`
- `app/services/gotrue_client.py:246`
- `app/services/gotrue_client.py:252`
- `app/services/otp_auth_service.py:96`

**What's wrong:** _find_user_by_email_and_update() does GET /admin/users with no page/per_page params (gotrue_client.py:246-250) and then next((u for u in users if u.email == email)) over that single response. GoTrue's /admin/users is paginated (default ~50 per page), so this only ever searches the first page. This path runs whenever create_admin_user hits the email_exists branch (gotrue_client.py:202-209) — i.e. every time a platform admin (re)invites or re-scopes an admin whose account already exists. Compounding it: the consumer OTP flow provisions a brand-new GoTrue account for EVERY email that ever requests a login code (otp_auth_service.py:96 -> ensure_consumer_user, POST /admin/users), so the user table grows to thousands of app_user rows quickly. Once total users exceed the first page, an existing admin will not be found and next() yields None, raising ValueError("User {email} not found") (gotrue_client.py:253-254), which surfaces as a 500 on the invite endpoint.

**Impact:** In production, re-inviting or re-scoping any existing admin fails with a 500 as soon as the GoTrue user count exceeds one page — which happens fast because every donor OTP request creates a user. An admin who needs their scope corrected or invite resent cannot be onboarded/fixed through the API.

**Recommendation:** Look the user up by email directly (GoTrue supports GET /admin/users?filter=/ ?email=, or the filter query) instead of listing; if listing is unavoidable, paginate until found. Do not rely on the first page.

---

### C3. 🟡 `MEDIUM` — Campaign "fully funded" milestone push can fire multiple times under concurrent completions

**Category:** `concurrency-race`

**Locations:**
- `app/services/donation_service.py:827`
- `app/services/donation_service.py:835`
- `app/services/donation_service.py:405`

**What's wrong:** _maybe_campaign_milestone runs after commit (outside the row/campaign lock) and computes crossed_now as `campaign.raised_amount >= target AND (campaign.raised_amount - donation.gross_amount) < target` (donation_service.py:835-839), reading campaign.raised_amount fresh from the DB (get_by_id at :832). That value is the LATEST global total, not this donation's own post-bump snapshot. When two donations complete near-simultaneously and each reads raised_amount after BOTH bumps have committed, both see the same final total; each subtracts only its own gross, and if both satisfy `final - own_gross < target <= final`, both evaluate crossed_now=True and both fan out CAMPAIGN_MILESTONE to every campaign donor. The bumps themselves serialize on the campaign row lock, but the post-commit milestone read is unlocked and unserialized, so the 'exactly once' guarantee in the docstring does not hold under concurrency.

**Impact:** For a popular campaign crossing its goal via overlapping donations, every donor of the campaign receives the 'Campaign fully funded' push two (or more) times. Notification spam, not a money error, but a user-visible correctness defect.

**Recommendation:** Determine the crossing atomically inside the completion transaction — e.g. capture the pre-bump and post-bump raised_amount from the same locked UPDATE ... RETURNING, and fire the milestone only when THIS transaction's own delta crossed the target; or set a `milestone_notified_at` flag on the campaign under the row lock and fire only the transaction that flips it.

---

### C4. 🟡 `MEDIUM` — create_admin_user ignores the result of the PUT that sets the admin's role/scopes, silently producing a role-less account that 403s on every request

**Category:** `error-handling`

**Locations:**
- `app/services/gotrue_client.py:214`
- `app/services/gotrue_client.py:216`
- `app/services/security-decode_token`

**What's wrong:** In the send_invite path, Step 1 (POST /invite) creates the GoTrue user with NO app_metadata.role, and Step 2 (PUT /admin/users/{id}, gotrue_client.py:216-221) is the only place the role/masjid_id/madrasha_id are set. That PUT is fired with no _raise_for_gotrue check on its response, and the whole step is skipped entirely when the invite response lacks an id (`if user_id:` at :215). So a transient failure or a missing id leaves the invited admin with a role-less identity, yet create_admin_user still returns user_data as success. decode_token (core/security.py:127-131) rejects any JWT without a role claim with 403 'Token missing role claim', so that admin can accept the invite and log in but is 403'd on every authenticated endpoint, with no signal to the inviting platform admin that provisioning half-failed.

**Impact:** A blip on the role-assignment PUT (or a GoTrue invite response without id) yields a broken admin account that cannot use the API at all, while the API reports the invite succeeded — a confusing, hard-to-diagnose onboarding failure.

**Recommendation:** Check the Step-2 PUT with _raise_for_gotrue and treat a missing user_id as a hard error (raise), so a failed role assignment surfaces instead of returning a false success.

---
