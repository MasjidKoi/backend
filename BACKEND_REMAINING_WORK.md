# Backend — What's Left for the Mobile PRDs

Gap analysis of the 8 mobile PRDs (`../mobile/docs/prds/`) against the actual backend
(`app/`). **Re-audited PRD-by-PRD on 2026-06-21** with one independent agent per PRD, each
extracting every backend requirement and verifying it against source (not against this doc).

> **Update 2026-06-21 — corrected after a full adversarial re-audit, then three fix passes.** The
> original pass claimed the backend was "feature-complete except PRD 02" — too optimistic. The
> re-audit confirmed PRDs **03/07/08 airtight** and **04 complete bar trivia**, but found real
> code gaps in **02, 05, 09**. Three fix passes have since closed them:
> **PR #17** — Q&A attribution, support-from-donation reference, broadcast audit-log, and
> nearby/search cache headers; **PR #18** — the full **data export** (PRD 09 #2), the
> **submission photo upload** (PRD 02 #5), and the committed **OTP test suite** (PRD 01);
> **PR #19** — the three Tier-1 compliance gaps: the 30-day **account-deletion purge** (PRD 09 #1),
> the **"donate anonymously by default" setting** (PRD 05 #3), and **per-message-type
> notification gating** (PRD 05/09 #4) — each with a migration and tests.

**Headline:** the backend is now **feature-complete** across all 8 PRDs. Every Tier-1 and Tier-2
code gap is closed. What remains is **non-code**: one minor hardening item (global `410 Gone`
enforcement), the **domain-gated** PRD 02 share/deep-link infra, and external config (SSLCommerz
prod creds, NBR tax flag, production domain, Expo FCM/APNs).

---

## Status at a glance

| PRD | Area | Status | What's left |
|---|---|---|---|
| 01 | Onboarding & Auth (email OTP) | ✅ **Done** | OTP suite + Bengali OTP email, GoTrue OTP-TTL, madhab vocab fix (2026-06-21) |
| 02 | Discovery & Map | 🟡 **share/deep-link only** | Share/OG page + `.well-known` (domain-gated). Submission photo upload landed (2026-06-21) |
| 03 | Prayer Times & **Push** | ✅ **Done (verified, with tests)** | Nothing |
| 04 | Profile / Photos / Q&A | ✅ **Done** | Q&A public attribution fixed (PR #17) |
| 05 | Donations & Dashboard | ✅ **Done (code)** | Donate-anon default + per-type push gating landed (PR #19). NGO config still external |
| 07 | Community (feed/reviews) | ✅ **Done (verified)** | Nothing |
| 08 | Gamification | ✅ **Done (verified)** | Nothing in backend scope |
| 09 | Settings & Accessibility | ✅ **Done (code)** | Deletion purge landed (PR #19); export widened (PR #18). Only global `410` hardening left |

---

## 🔴 Tier 1 — all closed ✅

### #1 — PRD 09: 30-day account-deletion purge — ✅ **FIXED (PR #19)**
`DELETE /users/me` still soft-deletes (flips `is_deleted` + stamps `deletion_requested_at`); a new
scheduled consumer now honours the 30-day promise. `AccountPurgeService.run_due()` finds every
soft-deleted account past `settings.ACCOUNT_PURGE_WINDOW_DAYS` and **anonymises** it — content rows
are re-keyed to a throwaway pseudonym (via `AccountPurgeRepository.anonymize_user`) so the masjids'
financial books stay intact, and the profile is reduced to a tombstone. Each account is purged in
its own transaction (one failure can't poison the sweep) and the `purged_at` stamp makes re-runs
idempotent. Wired into APScheduler **daily at 03:30 UTC** in `main.py`'s lifespan, gated on
`SCHEDULER_ENABLED` behind a Redis singleton lock. Covered by `tests/test_prd09_purge.py`.

### #2 — PRD 09: data export widened — ✅ **FIXED (PR #18)**
`GET /users/me/export` now carries every user-linked collection — donations, reviews, Q&A,
submissions, check-ins, journal entries, goals (+ completion dates), badges, support tickets,
device tokens, reports, recurring schedules, event RSVPs — on top of profile + follows. New
`app/schemas/user_export.py`; `UserService.export_me` aggregates per-user repo reads
(sequential, single session); added `list_all_for_user`/`list_for_user`/`list_rsvps_for_user`
to the repos that lacked one. No migration (read-only). Covered by `tests/test_prd09_export.py`.

### #3 — PRD 05: "donate anonymously by default" setting — ✅ **FIXED (PR #19)**
A `donate_anonymously_by_default` bool now lives on `UserProfile` (migration `…31a7`), exposed via
the notification-preferences schema/endpoints. `DonationService.create` seeds the per-donation
`is_anonymous` from it whenever the client sends no explicit value (`donation_service.py:187` —
`if is_anonymous is None: is_anonymous = profile.donate_anonymously_by_default`); an explicit
per-donation toggle still wins. Covered by `tests/test_donate_anonymous_default.py`.

### #4 — Per-message-type notification gating — ✅ **FIXED (PR #19)**
`PushService` now gates the fan-out per message type. `_MUTE_COLUMN_BY_TYPE` maps each
`PushMessageType` to a `UserProfile` mute column (`mute_donation_nudge`, `mute_campaign_milestone`,
`mute_moderation_outcome`, `mute_promotions`), with an **import-time exhaustiveness check** that
fails loudly if a new push type is added without a gating decision. The user/platform fan-outs
filter recipients via `repo.list_*_tokens_not_muting(mute_column)` (`push_service.py:181-197`), so
a user can mute donation/Eid/submission/moderation pushes. Per-follow types (instant announcement,
digest, time-change) stay gated upstream by `notification_mode`. Covered by
`tests/test_push_gating.py`.

---

## 🟠 Tier 2 — smaller functional gaps

### #5 — PRD 02: submission photo upload — ✅ **FIXED 2026-06-21**
Added `POST /masjids/submissions/photo` (multipart, authenticated, rate-limited) mirroring the
community-photo validation (415/413/422); it stores the image under `submissions/{user}/…` and
returns `{photo_key, url}`, which the client puts on the existing create-submission body. The
submission responses now expose a computed `photo_url` so the submitter **and** the NGO review
queue can view the photo. No migration (`photo_key` already existed). Covered by
`tests/test_submission_photo.py`.

### #6 — PRD 04: public Q&A answer attribution — ✅ **FIXED 2026-06-21**
`QuestionPublic` omitted `answer_author_role`, so the client couldn't render "answered by the
masjid" vs "the NGO" (US 38). The column was already stored and populated at answer time; this
pass added `answer_author_role: str | None` to `app/schemas/masjid_question.py` (no migration —
the column already exists on the model). **Done.**

### #7 — PRD 05: support-from-donation-detail (US 51) — ✅ **FIXED (PR #17)**
`SupportTicket` gained an optional `donation_id` (FK → `donations`, `ON DELETE SET NULL`) and a
`DonationIssue` category, so a ticket opened from a donation carries that context for the admin
(migration `cf15d14cf9cc`).

---

## 🟡 Tier 3 — process / minor

- **PRD 01:** ✅ **FIXED 2026-06-21** — the committed OTP pytest suite landed
  (`tests/test_otp_auth.py`, 12 cases: cooldown report, per-email/per-IP caps, 5-attempt lockout,
  expiry vs wrong-code classification, token+is_new_user, bootstrap-once, no-Redis degradation).
- **PRD 01 (full re-audit polish, 2026-06-21):** the PRD-by-PRD re-audit surfaced three smaller
  PRD 01 items, all now closed:
  - ✅ **Bengali OTP email (US #37)** — `app/email_templates/magic_link.html` was English-only;
    rewritten fully Bengali (`lang="bn"`, `১০ মিনিটে` expiry), and `GOTRUE_MAILER_SUBJECTS_MAGIC_LINK`
    set to `আপনার MasjidKoi লগইন কোড`. Brand wordmark + `{{ .Token }}` preserved.
  - ✅ **GoTrue OTP TTL** — added `GOTRUE_MAILER_OTP_EXP: 600` (+ `GOTRUE_MAILER_OTP_LENGTH: 6`) to
    `docker-compose.yml` so GoTrue's code validity matches the backend's 600 s `otp:issued` marker
    (`CODE_TTL_S`); previously GoTrue held codes ~1h while the API already returned `code_expired`.
  - ✅ **Madhab vocabulary** — `MadhabhType` (`app/schemas/user.py`) was a CamelCase / two-i
    `"Shafii"` `Literal`, diverging from the canonical lowercase `Madhab` enum the Asr calculator
    keys on (`prayer_calculator._ASR_MULTIPLIERS`). Replaced with a forgiving `BeforeValidator`
    normalizer (any case + `shafii→shafi` alias → canonical, invalid → 422). Profile madhab is
    display-only today, so this closes a latent footgun, not a live Asr bug. Covered by
    `tests/test_madhab_normalization.py`.
- **PRD 02:** ✅ **FIXED (PR #17)** — `Cache-Control: public, max-age=60` on `nearby`/`search`.
- **PRD 03:** ✅ **FIXED (PR #17)** — `POST /admin/broadcast-push` now writes an audit-log entry.
  (Still worth a one-line confirm with mobile that "follow == eligible for TIME_CHANGE ping.")
- **PRD 09:** `410 Gone` for a deleted account is enforced only on `/users/me*`, not globally —
  a soft-deleted user could still write via other routers (tangential; mobile drops to guest on
  the 202). *Still open.*

---

## Non-code / external dependencies (config, not engineering)

- **SSLCommerz** sandbox + production credentials (`SSLCOMMERZ_*`) — code reads them already.
- **NBR tax flag** — flip `tax_deductible_receipts_enabled` once NGO approval confirmed.
- **Production domain** — required for the PRD 02 share page + `.well-known` universal/app links.
- **Expo** — access token in `.env` (`PUSH_ENABLED=true`); delivery + async receipt reaping live.
  Real device delivery still needs the mobile/EAS side to upload FCM creds + an APNs key.

---

## ✅ Confirmed solid (re-audit)

- **PRD 03 push + prayer times — verified, WITH tests** (`test_prd03_push.py`,
  `test_expo_push_transport.py`, `test_push_receipts.py`): Expo transport, TIME_CHANGE fan-out on
  all three prayer-write paths, follower→token mute-aware resolution, Hijri offset + public
  `/app-config` + change ping, platform broadcast, sync + async dead-token reaping.
- **PRD 07 community — verified, no gaps:** cursor-paginated mute-independent feed, review upsert
  + conditional-body rule + `edited` marker + self-delete + public read, per-follow notification
  mode, digest-hour, instant announcement notifier across all 3 publish paths, hourly digest
  bucketing with empty-suppression + daily idempotency, RSVP + attendee count, 100m PostGIS
  check-in, guest gating.
- **PRD 08 gamification — verified, no gaps:** all three badge families with correct tiers
  (Fajr Warrior, Generous Giver, Community Pillar), both goal kinds + three templates, field-level
  journal upsert with backfill lock, real test suite. Lapse nudge / weekly reflection correctly
  device-local; dua/hadith copy is mobile-owned.
- **PRD 05 donations — money core verified, 56 tests:** SSLCommerz create/IPN/refund, idempotent
  COMPLETED-on-IPN with row-lock + cross-check, receipts (PDF, gapless numbering, tax-flag-gated),
  recurring schedule + nudge, admin balances/disbursement/refund, all four pushes, Generous Giver
  feed. Former gaps #3/#4/#7 all closed (PRs #17/#19).
- **PRD 04 profile/photos/Q&A — verified:** profile data, community-photo pipeline + moderation,
  Q&A subsystem, suggest-an-edit, shared 7-day moderation routing predicate.
- **PRD 01 auth — verified:** OTP request/verify, cooldown/caps/lockout/TTL, profile bootstrap,
  consumer role isolation, refresh/logout/profile/follow reuse.
