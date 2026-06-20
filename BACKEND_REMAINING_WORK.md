# Backend — What's Left for the Mobile PRDs

Gap analysis of the 8 mobile PRDs (`../mobile/docs/prds/`) against the actual backend
(`app/`). Re-verified file-by-file on **2026-06-21**.

> **Update 2026-06-21 — PRD 03 push event layer, PRD 08 goals, and the last cleanups LANDED.**
> Since the 2026-06-20 pass: the **PRD 03 push event layer** shipped (TIME_CHANGE fan-out on
> all prayer-write paths, follower→token audience resolution, Hijri offset column + validator +
> a **public `/app-config` GET** + `HIJRI_OFFSET` ping, and `PLATFORM_PUSH` broadcast via
> `/admin/broadcast-push`); the **PRD 08 goals subsystem** shipped (two goal kinds, template
> instantiation, journal-fed pace) with **Community Pillar** now counting accepted info reports;
> the **Expo `/getReceipts` dead-token reaper** (the #0 follow-up) shipped; and the redundant
> create-review route (PRD 07) was removed. All with migrations and tests.

**Headline:** the backend is **feature-complete for every PRD except the PRD 02 share /
deep-link infrastructure**, which is blocked on an external production-domain dependency.
Everything else that remains is non-code configuration. PRD 09 needs **zero** backend work.

---

## Status at a glance

| PRD | Area | Status | What's left |
|---|---|---|---|
| 01 | Onboarding & Auth (email OTP) | ✅ **Done** | Nothing (TOTP/aal2 intentionally off, out of scope) |
| 02 | Discovery & Map | 🟡 Mostly done | **Share/OG page + `.well-known` files — the only remaining backend feature; gated on a production domain** |
| 03 | Prayer Times & **Push** | ✅ **Done** | Nothing — transport, event layer, Hijri offset, platform push, and receipt reaping all landed |
| 04 | Profile / Photos / Q&A | ✅ **Done** | Nothing |
| 05 | Donations & Dashboard | ✅ **Done** | Only NGO config (SSLCommerz creds, NBR flag) — not code |
| 07 | Community (feed/reviews) | ✅ **Done** | Nothing — redundant create-review route removed |
| 08 | Gamification | ✅ **Done** | Nothing — goals subsystem + Community Pillar (check-ins, photos, accepted reports) all built |
| 09 | Settings & Accessibility | ✅ **Done** | Nothing — deletion/export/profile endpoints exist and are reused |

---

## 🟡 #1 — PRD 02 share / deep-link infrastructure (the only remaining backend feature)

Gated on the external **production-domain** dependency (PRD 02 notes); build the route/files
behind it once a domain exists.

- **Public OG-preview page** — no HTML/share route anywhere. Build a no-auth, cacheable
  `GET /masjids/{id}/share` (or similar) serving minimal HTML with OG meta tags
  (name, cover photo, address) + app-open redirect with store fallback.
- **`.well-known` association files** — neither `assetlinks.json` (Android App Links) nor
  `apple-app-site-association` (iOS Universal Links) exists or is served.

---

## Non-code / external dependencies (config, not engineering)

- **SSLCommerz** sandbox + production credentials (`SSLCOMMERZ_*`) — code reads them already.
- **NBR tax flag** — flip `tax_deductible_receipts_enabled` once NGO approval confirmed.
- **Production domain** — required for #1 above (share page + universal/app links). Once a domain
  exists, the `.well-known` files also unblock the mobile deep-linking work.
- **Expo** — access token in `.env` (`PUSH_ENABLED=true`); delivery is live, including async
  receipt reaping. Real device delivery still needs the mobile/EAS side to upload FCM creds + an
  APNs key (Expo relays to APNs/FCM; no backend Firebase/APNs plumbing required).

---

## ✅ Shipped (history)

- **PRD 03 push subsystem — DONE.**
  - **Transport (#0):** `app/services/expo_push_transport.py` — real Expo Push wire, config-gated
    by `PUSH_ENABLED`, batches ≤100/request, reaps synchronous `DeviceNotRegistered` tickets.
  - **Async receipt reaping (#0 follow-up):** `push_receipts` table + the `reap_push_receipts`
    scheduler job poll Expo `/getReceipts` ~15 min after send and prune tokens whose receipt
    reports `DeviceNotRegistered` (failures that never appear in the synchronous ticket).
  - **Event layer (#1):** `TIME_CHANGE` fan-out on `manual_override` / `recalculate` /
    `update_jumah`; follower→device-token audience resolution; Hijri offset
    (`platform_settings.hijri_offset_days`, −2…+2 validator, public `GET /app-config`,
    `HIJRI_OFFSET` change ping); `PLATFORM_PUSH` broadcast (`PushService.notify_all` /
    `broadcast_platform_push` + `POST /admin/broadcast-push`).
- **PRD 08 gamification — DONE.** StreakEngine, BadgeEngine, journal (field-level upsert +
  backfill window), redefined streak/badge read endpoints, **Generous Giver active**, and:
  - **Goals + templates** — `user_goals` / `goal_completions`; `quran_quantity` (journal-fed,
    recomputed daily pace) and `recurring` (idempotent daily/weekly check-off) kinds; template
    instantiation (Khatm-in-Ramadan, daily Ayat al-Kursi, weekly Surah al-Kahf); full CRUD +
    pause/abandon under `/users/me/goals`.
  - **Community Pillar** — counts check-ins, approved community photos, **and accepted (resolved)
    info reports** (`masjid_reports.user_id` + optional-auth on the public report endpoint +
    badge re-eval on resolve).
- **PRD 02/04 approval pushes — DONE & delivering:** `SUBMISSION_APPROVED`, `PHOTO_APPROVED`,
  `QNA_ANSWERED`.
- **PRD 05 donations — DONE:** full subsystem incl. all four donation pushes; only NGO config left.
- **PRD 07 community — DONE:** feed, review upsert + conditional-body rule, review self-delete,
  per-masjid notification mode + digest hour, announcement-instant notifier, digest scheduler,
  RSVP + attendee count, 100m PostGIS check-in. The legacy create-only `POST /masjids/{id}/reviews`
  was **removed** (the upsert `PUT` supersedes it).
