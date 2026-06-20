# Backend API Gaps — derived from Mobile PRDs 01–09

> **Purpose:** A pick-up-ready backlog of every backend change the mobile PRDs
> (`mobile/docs/prds/01..09`) require but the backend does **not** yet have.
> Each item states what exists today, what's missing, the proposed contract, and
> its blocking relationships.
>
> **Method:** Each gap below was verified against the actual code in `backend/app`
> (routers, models, schemas, services) as of 2026-06-16 — not inferred from the PRD
> alone. Where a PRD claims something "already exists," that claim was checked.
>
> **Conventions to follow** (from `backend/CLAUDE.md`): model → migration → repository
> → service → router; Pydantic schemas at the boundary; PostGIS via GeoAlchemy2;
> never block the event loop; commit explicitly in the service layer.

---

## Summary table

| # | Subsystem | PRD(s) | Status | Size | Blocks |
|---|-----------|--------|--------|------|--------|
| 1 | Consumer email-OTP auth | 01 | ✅ Done (branch) | M | All login-gated mobile actions |
| 2 | Nearby payload extension (coords/facilities/cover) | 02 | ✅ Done | S | **Map pins cannot render at all** |
| 3 | Search location bias + distance | 02 | ✅ Done | S | Search ranking |
| 4 | Masjid submissions pipeline | 02 | ✅ Done | L | Add-a-missing-masjid |
| 5 | Public share / OG-preview page + deep-link files | 02 | ❌ Missing | S | Shareable masjid links |
| 6 | Push subsystem (device tokens + fan-out + platform push) | 03 | ⚠️ Partial | L | Pushes in 02/03/04/05/07 |
| 7 | Hijri offset + public app-config read | 03 | ❌ Missing | S | Correct Hijri/Ramadan/Eid |
| 8 | Community photo submission pipeline | 04 | ✅ Done | M | Visitor photos |
| 9 | Masjid Q&A subsystem | 04 | ✅ Done | M | Ask-the-masjid |
| 10 | Moderation routing predicate (shared) | 04 | ✅ Done | S | Photo + Q&A queues |
| 11 | Donation subsystem (ledger, gateway, IPN, receipts, recurring, disbursement) | 05 | ❌ Missing | XL | The entire Donate promise |
| 12 | Followed-masjids feed endpoint | 07 | ✅ Done | M | Feed tab |
| 13 | Review upsert (PUT) + edited marker + low-star body rule | 07 | ✅ Done | S | Edit-your-review |
| 14 | Per-masjid notification mode + per-user digest hour | 07 | ✅ Done | S | Instant/digest/mute |
| 15 | Announcement publish notifier (instant fan-out) | 07 | ✅ Done | S | Instant announcement push |
| 16 | Digest scheduler (hourly bucketing job) | 07 | ✅ Done | M | Daily digest push |
| 17 | Gamification rework: journal-derived streak | 08 | ⚠️ Rework | M | Honest streak |
| 18 | Gamification rework: structured journal + field-level upsert + v0 migration | 08 | ⚠️ Rework | M | Prayer logging |
| 19 | Gamification rework: tiered badges | 08 | ⚠️ Rework | S | Milestone badges |
| 20 | Goals (templates + tracking) | 08 | ❌ Missing | M | Khatm-in-Ramadan |

**PRD 09 (Settings & Accessibility): no backend work.** `DELETE /me`, `GET /me/export`,
and profile/madhab update all exist and are consumed as-is. Verified in
`app/routers/users.py` (`/me` GET/PATCH/DELETE, `/me/export`). Nothing to do.

Legend: ✅ shipped · ❌ entirely absent · ⚠️ exists but wrong shape / partial · S/M/L/XL rough size.

### Implemented so far

- **PRD 02 — #2, #3, #4** (commits `c167c4b`, `8fa7ba1`, `aad13c2`): nearby payload
  now carries coords + facilities + cover photo; search is location-biased with
  distance; the masjid-submissions pipeline (model, user endpoints, admin
  approve/reject) is live. **#4's approval push** rides #6.
- **PRD 04 — #8, #9, #10** (commits `c8d42a2`, `dbd62d4`, `d87b31f`): community
  photo submission lifecycle, the Ask-the-masjid Q&A subsystem, and the shared
  moderation-routing predicate.
- **PRD 07 — #12, #13, #14, #15, #16** (this slice): the followed-masjids feed
  endpoint, review upsert (PUT) with edited marker + low-star body rule,
  per-masjid notification mode + per-user digest hour, the announcement
  instant-publish notifier, and the hourly Asia/Dhaka digest scheduler.
  Migration `c3f7a9e21b08`. First backend test suite added under `tests/`
  (feed contract + digest bucketing, both committed targets, + review upsert).
- **#6 push (⚠️ Partial):** a minimal-but-real push **core** shipped to unblock
  #15/#16 — device-token registry (`POST/DELETE /users/me/devices`), a `PushService`
  fan-out with a message-type discriminator, resolving user_ids → device tokens.
  The wire transport is a `LoggingTransport` no-op (records intended sends) until
  FCM/APNs credentials land. **Still missing for #6 to be Done:** the real
  Expo/FCM transport, silent prayer-time/Hijri pings, and the platform-wide
  push action. #4's submission-approved and #8/#9's photo/Q&A pushes can now ride
  this core once their hooks are added.

---

## 1 — Consumer email-OTP authentication (PRD 01) — ✅ Done (branch `feat/consumer-email-otp-auth`, commit `92e6e76`, not yet merged)

> **Shipped on its own branch:** `POST /auth/otp/request` + `POST /auth/otp/verify`,
> `otp_auth_service.py`, GoTrue email-OTP client methods, the magic-link email
> template, and the docker-compose mailer wiring. Merge that branch to land it here.

**Today:** `app/routers/auth.py` proxies GoTrue for **admin** auth only:
`/auth/login` (email+password), `/refresh`, `/logout`, `/user/password`, `/2fa/*`,
`/password/reset`, `/admin/invite`, `/co-admin/accept|decline`. There is **no
passwordless email-OTP flow** for consumers.

**Missing:**
- `POST /auth/otp/request` — body `{email}`; **always returns 202** (no account
  enumeration); returns resend-cooldown seconds remaining when called inside the
  cooldown window. Backed by GoTrue's native email-OTP.
- `POST /auth/otp/verify` — body `{email, code}`; returns `{access_token,
  refresh_token, token_type, expires_in, is_new_user}`. Response must distinguish
  **wrong-code (n attempts left)**, **code-expired**, and **too-many-attempts
  (request a fresh code)** as separate states.
- **OTP policy** on the existing Redis rate-limit layer (`app/core/rate_limit.py`):
  60s per-email resend cooldown, per-email + per-IP hourly send caps, 5-attempt
  verify lockout, 10-min code TTL, new code invalidates old.
- **Profile-row bootstrap:** first successful verify creates the `user_profiles`
  row with all fields null (madhab set later, client-confirmed).
- **Verification task (not new code):** confirm admin-gated dependencies reject
  consumer JWTs (`app_user` role). The dependencies already gate on role.

**Reused as-is:** `/auth/refresh`, `/auth/logout`, profile get/update, follow/unfollow.

---

## 2 — Nearby payload extension (PRD 02) — ✅ Done — **blocks all map pins**

**Today:** `GET /masjids/nearby` returns `list[MasjidNearbyResult]`, which is
`MasjidSummary + distance_m`. `MasjidSummary` carries `masjid_id, name, address,
admin_region, status, verified, donations_enabled, created_at, updated_at` —
**no latitude/longitude, no facility booleans, no cover-photo URL** (verified in
`app/schemas/masjid.py`). Facility filters already exist as query params, but the
response cannot place a pin on a map.

**Missing — extend the nearby result schema with static columns only:**
- `latitude`, `longitude` (from the PostGIS `location`)
- the six facility booleans: `has_sisters_section`, `has_wudu_area`,
  `has_wheelchair_access`, `has_parking`, `has_janazah`, `has_school`
- `cover_photo_url` (the `is_cover` photo, or null)

Keep it cacheable; no per-masjid prayer times in this payload (mobile fetches those
lazily per peek card). `verified` is already present — keep it.

---

## 3 — Search location bias + distance (PRD 02) — ✅ Done

**Today:** `GET /masjids/search?q=` takes only `q` and returns `list[MasjidSummary]`
— no location awareness, no distance (verified in `app/routers/masjids.py:120`).

**Missing:** optional `lat`/`lng` query params; when present, rank by match quality
then distance and include a `distance_m` field in results, reusing the existing
PostGIS machinery. Ships in the same PR as #2.

---

## 4 — Masjid submissions pipeline (PRD 02) — ✅ Done (approval push pending #6)

**Today:** nothing — no submissions model, router, or service.

**Missing:**
- **`masjid_submissions` table** — separate from the live `masjids` table by
  construction (unreviewed data must never reach public queries). Fields: id,
  submitter `user_id`, name, lat/lng, optional address text, optional photo key,
  `status` (pending/approved/rejected), `approved_masjid_id` (nullable), timestamps.
- **`POST /masjids/submissions`** — authenticated; per-user pending cap (~3) and
  rate limit via the existing rate-limit layer. Mandatory: name + coordinates.
- **`GET /me/submissions`** — returns status enum + the approved masjid's id (so
  "view it live" works).
- **Admin: list / approve / reject.** Approve creates a real masjid through the
  existing platform-admin create path (`POST /masjids`) and triggers the
  submitter's push (depends on #6).
- Dedupe uses the existing public nearby endpoint (~150 m) client-side — **no new
  dedupe API needed.**

---

## 5 — Public share / OG-preview page + deep-link association files (PRD 02)

**Today:** nothing — `app/main.py` mounts API routers only.

**Missing:**
- A **public, unauthenticated, cacheable** masjid OG-preview route serving minimal
  HTML with Open Graph meta tags (name, cover photo, address) plus an app-open
  redirect that falls back to the store.
- The two static **well-known association files** (Android App Links
  `assetlinks.json`, iOS `apple-app-site-association`) served from the production
  domain.
- Static OG content only — no live prayer times in previews.
- **Open dependency:** a production domain (the only thing blocking this feature).

---

## 6 — Push subsystem (PRD 03) — ⚠️ Partial (minimal core shipped) — **load-bearing for 02/03/04/05/07**

> **Shipped:** device-token registry (`POST /users/me/devices`, `DELETE
> /users/me/devices/{token}`), a `PushService` fan-out resolving user_ids →
> device tokens, and the message-type discriminator (`PushMessageType`) wired
> for the two PRD 07 types. The wire transport is a `LoggingTransport` no-op
> until FCM/APNs creds exist. **Still missing:** the real Expo/FCM transport,
> the silent prayer-time/Hijri pings, and the platform-wide push action.

**Today:** nothing — no device-token model, no fan-out service, no push code
anywhere (verified: no `device`/`push`/`token`/`fcm`/`notif` files).

**Missing — built from scratch:**
- **Device-token registration endpoint** — `{token, platform, home_masjid_id?,
  favourite_masjid_ids?}`; upserted, idempotent per (device, token), tolerant of
  token rotation; associations update on home-masjid change and favourite
  add/remove; pruned on logout.
- **Fan-out service** — sends **silent data pings** to affected devices when prayer
  times are written (hook the existing manual-update and recalc paths in the prayer
  service) and when the Hijri offset changes (#7).
- **Platform-admin "send platform-wide push"** action (reusable for Eid /
  Ramadan-start / urgent notices).
- **Message-type discriminator** — a stable field PushLink routes on. Message types
  accumulated across PRDs: time-change ping, hijri-offset ping, platform push
  (03); submission-approved (02); photo-approved, qna-answered (04);
  donation-confirmed, payment-recovery, recurring-nudge, campaign-milestone (05);
  announcement-instant, daily-digest (07).
- Transport (Expo Push vs bare FCM via firebase-admin) is the implementer's choice;
  token registration is required either way.
- **Open dependency:** Firebase project / FCM credentials / APNs config — none exist.

> If timelines slip: ship local notifications without pings first (freshness then
> holds only for foregrounded apps); add the ping layer next. The Eid platform push
> is the one piece with a hard calendar deadline.

---

## 7 — Hijri offset + public app-config read (PRD 03)

**Today:** `PlatformSettings` (`app/models/platform_settings.py`) has no
`hijri_offset_days`. Settings are exposed only through **admin-gated** `/admin/settings`.

**Missing:**
- `hijri_offset_days` column on `PlatformSettings` (validated −2…+2, default 0).
- A **public app-config read** so clients cache the offset without auth.
- The offset-change push ping (rides #6) so closed apps correct overnight.

---

## 8 — Community photo submission pipeline (PRD 04) — ✅ Done (approval push pending #6)

**Today:** admin masjid photos exist (`MasjidPhoto`, `PhotoResponse` with
`photo_id, url, is_cover, display_order, created_at`; admin upload / delete /
set-cover / reorder under `/masjids/{id}/photos`). There is **no community
submission lifecycle** — no `source`, `status`, or `uploaded_by`.

**Missing:**
- Extend the photo model with `source` ('admin' | 'community'), `status`
  ('pending' | 'approved' | 'rejected'), nullable `uploaded_by` (SET NULL on user
  deletion). **Backfill** existing rows as admin/approved.
- **Community upload endpoint** — authenticated, rate-limited (~3/masjid/day,
  ~10/day/user, 5 MB each), always created `pending`. Rate-limit responses must be
  distinguishable from validation failures.
- **Approve / reject** endpoints — authorised for the masjid's admin + platform
  admins (uses #10's routing).
- **Public paginated listing** of approved community photos, **separate** from the
  profile's admin gallery (the profile read keeps returning admin photos only).
- **`GET /me/photo-submissions`** with status + timestamps.
- Approval push (rides #6).

---

## 9 — Masjid Q&A subsystem (PRD 04) — ✅ Done (answered push pending #6)

**Today:** nothing.

**Missing:**
- **`masjid_questions` table** — masjid, asker `user_id`, question text, `status`
  (pending → answered | rejected), answer text, answerer + **author-role field**
  (so community answers can open later without a schema change), timestamps.
- **`POST /masjids/{id}/questions`** — authenticated, length-validated, rate-limited.
- **Public listing** of **answered-only** questions per masjid (a profile must
  never read "14 questions · 0 answers").
- **`GET /me/questions`** — the asker's own pending/answered/rejected with timestamps.
- **Answer endpoint** — sets answer + status atomically, fires the asker's push
  (rides #6). **Reject endpoint** — visible to asker only, no public trace, no push.
  Both authorised like photo moderation (#10).

---

## 10 — Moderation routing predicate (PRD 04) — ✅ Done — shared by #8 and #9

**Today:** nothing.

**Missing:** one **pure** predicate over `(item, masjid-has-claimed-admin,
pending-since, now)` deciding queue visibility: route to the masjid admin's queue
when the masjid has a claimed admin, else to the NGO central queue; anything pending
> **7 days** also becomes visible to the NGO (shared visibility, not a handoff).
Implement **once**, used identically by both moderation list endpoints so the 7-day
rule can never drift between content types.

---

## 11 — Donation subsystem (PRD 05) — **XL; the entire Donate promise**

**Today:** `DonationStatus` and `DonationCategory` enums exist in
`app/models/enums.py`, and campaigns exist (`masjid_campaigns` with a
`raised_amount` column that **has no writers**). There is **no donation, recurring,
disbursement, or receipt code anywhere.** This is the single largest gap.

**Missing — follow PRD 05's "Backend Design" section verbatim. Build order (tracer
bullet):**

1. **Models + migration + enum wiring** — four new tables:
   - `donations` (donation_id PK doubling as gateway `tran_id`; user_id no-FK;
     masjid_id FK **RESTRICT**; campaign_id nullable FK RESTRICT; category; status;
     `gross_amount`/`fee_amount`/`net_amount` Numeric(12,2) with `net = gross − fee`
     CHECK and 10–500000 bound; `is_anonymous`; gateway fields; `gateway_val_id`
     **UNIQUE** = idempotency key; timestamps). DB CHECK
     `(category='campaign') = (campaign_id IS NOT NULL)`.
   - `recurring_schedules` (no FK from donations — it's a reminder engine only).
   - `disbursements` (balance is **derived, never stored**).
   - `donation_receipt_counters` (gapless per-year numbers).
   - Platform-settings key `tax_deductible_receipts_enabled` (default false).
2. **SslcommerzGateway** (deep adapter, sibling of the GoTrue client) against the
   **sandbox** — create-session → URL, validate-IPN → verdict, refund.
3. **DonationLedger** service — state machine
   `PENDING → COMPLETED → REFUNDED` / `→ FAILED`; completion bumps campaign
   `raised_amount` + ledger balance atomically; idempotent on `gateway_val_id`.
4. Refund, disbursements, derived balance.
5. History / summary endpoints.
6. Recurring schedules + two APScheduler sweeps (nudge on due cycles; stale-pending
   → FAILED + one recovery push) on the existing scheduler.
7. Receipts (PDF via `run_in_executor`, NBR wording gated by the platform flag).

**API contract** (see PRD 05 for full detail):
```
POST   /masjids/{id}/donations            create PENDING + gateway URL
POST   /campaigns/{id}/donations          campaign-attributed (masjid derived from campaign)
GET    /donations/{id}                    status poll (owner-only)
GET    /donations/{id}/receipt            PDF (completed only)
GET    /me/donations?masjid_id&category&status&year&cursor
GET    /me/donations/summary              lifetime / yearly / per-masjid totals
GET    /me/donations/annual-report?year   giving-summary PDF
POST   /me/recurring-schedules            (incl. last-10-nights preset)
GET    /me/recurring-schedules
PATCH  /me/recurring-schedules/{id}       pause / resume / amount
DELETE /me/recurring-schedules/{id}       cancel
POST   /payments/sslcommerz/ipn           UNAUTH, validates everything — only writer of COMPLETED
GET    /payments/sslcommerz/redirect/{outcome}   success/fail/cancel → deep link
GET    /admin/masjids/{id}/donations      anonymity mask applied in query layer
GET    /admin/masjids/{id}/balance        derived
GET    /admin/balances                    platform_admin
POST   /admin/masjids/{id}/disbursements  record payout (platform_admin, AAL2)
POST   /admin/donations/{id}/refund       gateway refund + reversal (platform_admin, AAL2)
```

- New push types ride #6; donation completion emits the event #19's BadgeEngine
  (Generous Giver) listens for.
- **Open dependencies (config, not code):** SSLCommerz sandbox creds (blocks step 2),
  the NBR tax-language flag.
- These would be the **first backend tests in the repo** (money path only).

---

## 12 — Followed-masjids feed endpoint (PRD 07) — ✅ Done

> **Shipped:** `GET /users/me/feed?type=announcements|events`, cursor-paginated
> (tuple-comparison cursors for page stability), embedding masjid id + name;
> event items embed attendee count + the caller's RSVP state and exclude past
> events server-side (Asia/Dhaka). Mute does not affect the feed.
> `app/routers/feed.py` · `feed_service.py` · `feed_repository.py`. Covered by
> `tests/test_feed.py` (committed target).

**Today:** announcements and events exist per-masjid; there is **no aggregated
feed** across a user's follows.

**Missing:** one authenticated, **type-parameterised**, cursor-paginated endpoint
joining the caller's follows to:
- `type=announcements` — published only, `published_at` descending
- `type=events` — `starts_at` ascending, **past events excluded server-side**

Each item **embeds the masjid id + display name** (cards render without follow-up
calls); event items embed attendee count + the caller's RSVP state.

---

## 13 — Review upsert + edited marker + low-star body rule (PRD 07) — ✅ Done

> **Shipped:** `PUT /masjids/{id}/reviews` create-or-replace, `edited` stamped on
> replacement, 1–2★ require a ≥20-char body (clean 422), `edited` + `updated_at`
> added to `MasjidReviewResponse`. Delete reuses the existing endpoint. Covered
> by `tests/test_review_upsert.py`.

**Today:** reviews are **create-only** — `POST /masjids/{id}/reviews` (409 on
duplicate), `GET /reviews`, `DELETE /reviews/{review_id}`. The one-review-per-user
constraint exists. `MasjidReviewResponse` has no `edited` marker (verified in
`app/schemas/masjid_review.py`). Editing today means delete-and-recreate.

**Missing:**
- **`PUT /masjids/{id}/reviews`** — idempotent "put my review": create or fully
  replace the caller's single review, stamping an **edited** marker on replacement.
- **Conditional body validation:** 1–2 stars require a short body (~20 chars min);
  3–5 stars may be stars-only.
- Add the `edited` field to the response. Delete reuses the existing endpoint.

---

## 14 — Per-masjid notification mode + per-user digest hour (PRD 07) — ✅ Done

> **Shipped:** `notification_mode` on the follow (digest default, existing rows
> backfilled), `digest_hour` (0–23, default 19) + `last_digest_sent_at` on the
> user. Contracts: `GET/PATCH /users/me/notification-preferences` and
> `PATCH /masjids/{id}/follow`.

**Today:** `UserMasjidFollow` has only `(user_id, masjid_id, followed_at)` — no
notification mode. `UserProfile` has no digest-hour field (both verified).

**Missing:**
- `notification_mode` on the follow relationship — `digest` (default) | `instant` |
  `mute`; default existing rows to `digest`.
- `digest_hour` on the user (0–23, default 19), interpreted in **Asia/Dhaka**.
- Read/update contracts for both.

---

## 15 — Announcement publish notifier (PRD 07) — ✅ Done

> **Shipped:** instant-mode follower fan-out wired into all three publish paths
> (direct-publish, draft→publish, scheduled auto-publish) via #6's `PushService`,
> best-effort so a push failure never rolls back the publish. Unpublish/edit send
> nothing. Rides #6's `LoggingTransport` until a real transport lands.

**Today:** the publish action exists (`POST
/masjids/{id}/announcements/{id}/publish`) but does **not** notify followers (no
push subsystem to ride).

**Missing:** a publish-time hook that resolves **instant-mode** followers' devices
through #6's token registry and fans out (announcement-instant message type).
Unpublish / edit send nothing.

---

## 16 — Digest scheduler (PRD 07) — ✅ Done

> **Shipped:** hourly cron job (`send_daily_digests`) on the existing scheduler;
> `DigestService.run_for_hour(hour, now)` does Asia/Dhaka hour-bucketing,
> digest-mode-only collection (24 h bounded), empty-digest suppression, and
> one-push-per-user-per-day idempotency via `last_digest_sent_at`. Covered by
> `tests/test_digest.py` (committed target: bucketing/routing/suppression/idempotency).

**Today:** APScheduler exists (`app/core/scheduler.py`, used by announcement
publishing) but no digest job.

**Missing:** an **hourly bucketing** job — each run serves users whose chosen
`digest_hour` matches the current Asia/Dhaka hour; collect announcements published
since the user's last digest (bounded 24 h) from **digest-mode** follows; if none,
send nothing; one push/user/day summarising count + masjid count, deep-linking to
the Feed tab. A per-user last-digest-sent timestamp makes it idempotent across
restarts. (Committed test target.)

---

## 17–19 — Gamification rework (PRD 08) — ⚠️ the deployed v0 measures the wrong thing

**Today (verified in `app/schemas/gamification.py` + `routers/gamification.py`):**
- `StreakResponse = {current_streak, total_checkins}` — **check-in-derived** (the
  model the PRD explicitly rejects: excludes home prayers, penalises women & rural
  users).
- Journal: free-text `prayers_logged` string + `quran_pages` int + `notes`; the
  upsert does **whole-row replacement**.
- `BadgeResponse = {badge_id, badge_type, earned_at}` — **no tier**; one row per
  badge type.
- **No goals.**

**17 — Journal-derived streak:** redefine the streak contract to journal-derived
prayer-log streaks; response gains `current` / `longest` / freeze fields and **drops
`total_checkins`** (moves to the check-in history contract). Implement the rules in a
pure **StreakEngine** (all-5-or-nothing day; noon-next-day **Asia/Dhaka**
finalization; earned/automatic/never-sold freezes, derived not stored; protected-day
pass-through identical for freeze and exempt). Server day boundaries move from
`date.today()` to Asia/Dhaka. (Committed test target.)

**18 — Structured journal + field-level upsert + migration:** `prayers_logged`
becomes a structured five-prayer boolean set; `quran_pages` generalises to
amount + unit; add a protected-day marker. Replace whole-row upsert with
**field-level** updates (a Qur'an edit must not clear prayer logs). Enforce the
backfill window (streak-locked dates reject prayer edits; notes/Qur'an stay
editable). **Migrate v0 free-text rows** by token-parsing where recognisable, else
preserve into notes. (Committed test target.)

**19 — Tiered badges:** add a `tier` dimension; uniqueness becomes one row per
(user, badge_type, tier). Pure **BadgeEngine**: Fajr Warrior 7/40/100 consecutive
logged-Fajr; Generous Giver 3/6/12 consecutive giving-months (**ships dormant**,
activates when #11 lands — criterion encoded now); Community Pillar from accumulated
verified contributions. Idempotent, no skipped tiers. (Committed test target.)

---

## 20 — Goals (PRD 08)

**Today:** nothing.

**Missing:** goals model + CRUD with **template instantiation** —
Khatm-in-Ramadan (604 pages across Ramadan, daily pace recomputed from
remaining/remaining-days), daily Ayat al-Kursi, weekly Surah al-Kahf on Jumu'ah;
free-form goals (name, target, unit, period) allowed. Qur'an-unit goals consume
journal entries automatically; pause/abandon without ceremony.

> **Rollout note (PRD 08):** R1 (~Dec 2026) = journal + logging + streak with
> exempt-mode & freezes from day one (#17, #18). R2 (~Jan 2027) = tiered badges,
> goals, reflection/nudge notifications (#19, #20). The v0 reconciliation is R1's
> first task.

---

## Suggested implementation order

1. **#1 (OTP auth)** ✅ done on branch `feat/consumer-email-otp-auth` (merge pending) — gates every other consumer action.
2. **#2 + #3 (nearby payload + search bias)** ✅ — one PR; #2 unblocks the entire map.
3. **#6 (push subsystem) ⚠️ minimal core done + #7 (Hijri offset) ❌** — load-bearing for 4 later PRDs; real FCM/APNs transport + platform push still pending.
4. **#13 + #14 + #15 + #16 + #12 (community/feed)** ✅ — PRD 07 vertical slices; #15/#16 ride #6's minimal core.
5. **#8 + #9 + #10 (photo pipeline + Q&A + routing)** ✅ — PRD 04 backend-first; #10 shared.
6. **#4 ✅ + #5 ❌ (submissions + share)** — #4 shipped (approval push pending #6's real transport); #5 needs a production domain.
7. **#17–#20 (gamification rework)** ❌ — independent track on the R1/R2 calendar above.
8. **#11 (donations)** ❌ — XL, self-contained, needs #6 for pushes and SSLCommerz sandbox creds. Extends the now-existing test suite.

---

## Notes for the implementer

- **Recurring backend correction across all PRDs:** the feature doc says the backend
  is Node.js — it is **FastAPI/Python**. Contracts above are written against the
  actual backend.
- **Login-gate registry:** the mobile login wall now gates **eight** actions
  (Donate, Follow, Submit-a-masjid, Upload-photo, Ask-question, RSVP, Write-review,
  Check-in). That's a mobile concern, but every gated action has a backend endpoint
  above — make sure each requires auth.
- **PRD 09 needs nothing** — re-confirmed: deletion, export, profile/madhab all exist.
- Several PRDs name **committed backend tests** (DonationLedger/Gateway/Recurring,
  DigestScheduler, feed endpoint, StreakEngine, BadgeEngine, journal contracts).
  The **test suite now exists** under `backend/tests/` — established by PRD 07's
  feed + digest tests (`pytest` + `pytest-asyncio`, in-process httpx against the
  live Postgres with a `get_db` override; JWTs minted from `GOTRUE_JWT_SECRET`).
  PRD 05's money-path tests extend this pattern. Honour the committed-test
  commitments where a PRD makes them.
</content>
</invoke>
