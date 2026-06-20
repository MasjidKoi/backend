# Backend — What's Left for the Mobile PRDs

Gap analysis of the 8 mobile PRDs (`../mobile/docs/prds/`) against the actual backend
(`app/`), verified file-by-file on 2026-06-20.

> **Update 2026-06-20 — quick fixes landed.** Review self-delete (PRD 07 story 45) is
> done; the three contribution pushes (submission/photo/Q&A approval) are now **wired and
> firing** through `PushService` — they deliver for real the moment a push transport lands
> (see #0). Two stale docstrings corrected. Details struck through below.

**Headline:** the backend is ~90% built to these PRDs. Auth (01), the full donation
subsystem (05), profile/photos/Q&A (04), community/feed (07), and gamification core (08)
are implemented *with migrations and tests*. What remains is concentrated in **the push
delivery layer** and a few discrete features. PRD 09 needs **zero** backend work.

---

## Status at a glance

| PRD | Area | Status | What's left |
|---|---|---|---|
| 01 | Onboarding & Auth (email OTP) | ✅ **Done** | Nothing (TOTP/aal2 intentionally off, out of scope) |
| 02 | Discovery & Map | 🟡 Mostly done | ~~Submission-approval push~~ ✅ wired; share/OG page + `.well-known` files |
| 03 | Prayer Times & **Push** | 🟠 **Largest gap** | Time-change ping + audience resolution; Hijri offset (col + public config + ping); platform-wide push |
| 04 | Profile / Photos / Q&A | ✅ **Done** | ~~Photo + Q&A approval pushes~~ ✅ wired (deliver once transport lands) |
| 05 | Donations & Dashboard | ✅ **Done** | Only NGO config (SSLCommerz creds, NBR flag) — not code |
| 07 | Community (feed/reviews) | ✅ **Done** | ~~Review self-delete~~ ✅ done; (optional) drop redundant create-review route |
| 08 | Gamification | 🟡 Mostly done | **Goals + templates (whole subsystem)**; Community Pillar counter inputs |
| 09 | Settings & Accessibility | ✅ **Done** | Nothing — deletion/export/profile endpoints exist and are reused |

---

## 🔑 #0 — The keystone blocker: push delivery transport (cross-cutting)

`app/services/push_service.py` ships a **`LoggingTransport` — a no-op that only logs the
intended fan-out**. There is no real FCM / APNs / Expo sender anywhere in the repo.

Consequence: the pushes that *are* already wired and firing — **announcement-instant,
daily-digest, and all four donation pushes** (donation-confirmed, payment-recovery,
recurring-nudge, campaign-milestone) — currently deliver to a log line, not a phone. And
every push gap below is moot until this lands.

**What's left:** implement a real `PushTransport` (Expo Push Service matches the mobile
`expo-notifications` choice, or bare FCM via `firebase-admin`), wire credentials
(Firebase project + APNs config, or an Expo access token). External dependency flagged in
PRD 03's notes — none of this exists yet.

---

## 🟠 #1 — PRD 03 push event layer (the biggest feature chunk)

The push *infrastructure* (token registry, `notify_users` fan-out, message-type
discriminator) is real and proven in production paths. What's missing are the PRD 03
**callers and the offset feature**:

- **Time-change ping (`TIME_CHANGE`)** — no hook exists. Add a fan-out to
  `prayer_time_service.py` on all three write paths: `manual_override` (:240),
  `recalculate` (:331), `update_jumah` (:402). Carry masjid id + touched date range.
- **Audience resolution** — nothing maps an edited masjid → its home/favourite followers'
  device tokens. `device_tokens` stores only token+user_id+platform (no masjid columns);
  `list_tokens_for_users` resolves by user_id only. Build the join
  `follows → user_ids → device_tokens` (or add association columns per the PRD).
- **Hijri offset** — entirely missing:
  - `hijri_offset_days` column on `platform_settings` (validated −2…+2) + Alembic migration
  - a validator in `PlatformSettingsUpdate`
  - a **public app-config GET** to expose it (today `GET /admin/settings` is admin-only —
    clients can't read it)
  - fire a `HIJRI_OFFSET` ping on change
- **Platform-wide push (`PLATFORM_PUSH`)** — no endpoint, no broadcast helper. Add a
  `platform_admin` action in `admin.py` + a "broadcast to all tokens" method on
  `PushService` (reused for Eid / Ramadan-start / urgent notices — Eid has a hard calendar
  deadline).

*Already done & reused:* device-token register/prune endpoints, per-masjid prayer times
(`days=1..7`), Jumu'ah schedule, wall-clock `HH:MM` contract.

---

## ✅ #2 — Contribution pushes — DONE (wired; real delivery gated on #0)

All three approval pushes now construct a `PushMessage` and call
`PushService.notify_users` (best-effort, after commit), mirroring the announcement path:

- **PRD 02 `SUBMISSION_APPROVED`** — fired in `masjid_submission_service.approve()` to
  `submission.user_id`.
- **PRD 04 `PHOTO_APPROVED`** — fired in `community_photo_service._moderate()` on
  approval to `photo.uploaded_by` (guarded — `uploaded_by` is nullable on account deletion).
- **PRD 04 `QNA_ANSWERED`** — fired in `masjid_question_service.answer()` to
  `question.asker_user_id`, deep-linking to the answer.

They log via `LoggingTransport` today and deliver to devices once a real transport (#0) lands.

---

## 🟡 #3 — PRD 02 share / deep-link infrastructure (does not exist)

- **Public OG-preview page** — no HTML/share route anywhere. Build a no-auth, cacheable
  `GET /masjids/{id}/share` (or similar) serving minimal HTML with OG meta tags
  (name, cover photo, address) + app-open redirect with store fallback.
- **`.well-known` association files** — neither `assetlinks.json` (Android App Links) nor
  `apple-app-site-association` (iOS Universal Links) exists or is served.

Both are gated on the external **production-domain** dependency (PRD 02 notes); build the
route/files behind it once a domain exists.

---

## 🟡 #4 — PRD 08 gamification remainders

- **Goals + templates — entire subsystem MISSING.** No goal model/migration/schema/repo/
  service/endpoint. Needs: goals CRUD, template instantiation (Khatm-in-Ramadan with
  daily-pace recompute, daily Ayat al-Kursi, weekly Surah al-Kahf), and journal-fed Qur'an
  progress. **NOTE:** PRD 08 sequences this to **R2 (Jan 2027, pre-Ramadan)** — likely a
  deliberate deferral, not an oversight, but it is genuinely not built.
- **Community Pillar counter** — `gamification_service.py:279` counts **check-ins only**;
  the PRD also wants accepted info reports + approved community photos summed in (noted as
  not-yet-summed in a code comment).

*Already done:* StreakEngine (all-5/day, derived freezes, Dhaka noon finalization,
protected-day pass-through), BadgeEngine (tiers, idempotent, no-skip), journal field-level
updates + backfill-window enforcement, Dhaka day boundaries, redefined streak/badge read
endpoints — all with tests. **Generous Giver is ACTIVE** (wired to donation completion),
despite a stale docstring saying "dormant."

---

## 🟡 #5 — PRD 07 small fixes

- ✅ **Review self-delete — DONE.** `DELETE /masjids/{id}/reviews/{review_id}` now uses
  `get_current_user`; the service allows the review's author to delete it, falling back to
  the masjid-admin / platform-admin path for everyone else (PRD story 45).
- **(Optional cleanup)** the legacy create-only `POST /masjids/{id}/reviews` (409-on-duplicate)
  is redundant alongside the upsert `PUT` — left in place (removing it could break existing
  consumers); drop when confirmed unused.

*Already done & wired:* feed endpoint, review upsert + conditional-body rule, per-masjid
notification mode + digest hour, **announcement-instant notifier (wired, both publish
paths)**, **digest scheduler (registered, hourly Dhaka bucketing)**, RSVP + attendee count,
100m PostGIS check-in — all with tests.

---

## Non-code / external dependencies (config, not engineering)

- **SSLCommerz** sandbox + production credentials (`SSLCOMMERZ_*`) — code reads them already.
- **NBR tax flag** — flip `tax_deductible_receipts_enabled` once NGO approval confirmed.
- **Firebase project / APNs config / Expo token** — required for #0 (real push delivery).
- **Production domain** — required for #3 (share page + universal/app links).

## Cosmetic
- ✅ `PushMessageType` docstring corrected — now lists which types are wired (07/05/02/04)
  vs. still reserved (03), and notes the no-op transport.
- ✅ `BadgeEngine` docstring/comments corrected — Generous Giver marked active (PRD 05),
  not "dormant".

---

## Suggested order

1. **Real push transport + credentials** (#0) — unblocks everything push-shaped, incl.
   the already-wired announcement/digest/donation **and now submission/photo/Q&A** pushes.
2. **PRD 03 event layer** (#1) — time-change ping + audience resolution + Hijri offset +
   platform push (Eid deadline-sensitive).
3. **PRD 02 share infra** (#3) — when a domain is available.
4. **PRD 08 goals** (#4) — aligned to the R2 / pre-Ramadan window.

✅ Done this pass: contribution pushes (#2), review self-delete (#5), stale docstrings.
