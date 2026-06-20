# Backend — What's Left for the Mobile PRDs

Gap analysis of the 8 mobile PRDs (`../mobile/docs/prds/`) against the actual backend
(`app/`). **Re-audited PRD-by-PRD on 2026-06-21** with one independent agent per PRD, each
extracting every backend requirement and verifying it against source (not against this doc).

> **Update 2026-06-21 — corrected after a full adversarial re-audit.** The prior pass claimed
> the backend was "feature-complete for every PRD except PRD 02." That was **too optimistic**.
> The re-audit confirmed PRDs **03 and 07 are airtight (with tests)** and **04 and 08 complete
> bar trivia**, but found **genuine code gaps in PRDs 02, 05, and 09** that the previous summary
> missed — two of them compliance-critical. One small gap (PRD 04 Q&A attribution) was **fixed
> in this pass**. PRD 09 is **not** zero-work and PRD 05 is **not** config-only.

**Headline:** the backend covers the bulk of all 8 PRDs, but is **not** feature-complete.
Besides the known PRD 02 share/deep-link infra, there are real gaps in **account deletion +
data export (PRD 09)**, **donation privacy + push gating (PRD 05)**, and **submission photo
upload (PRD 02)**.

---

## Status at a glance

| PRD | Area | Status | What's left |
|---|---|---|---|
| 01 | Onboarding & Auth (email OTP) | ✅ **Functionally done** | The PRD-**committed** OTP pytest suite was never written (impl is complete) |
| 02 | Discovery & Map | 🟡 **2 gaps** | Share/OG page + `.well-known` (domain-gated) **and** submission photo upload has no backend path |
| 03 | Prayer Times & **Push** | ✅ **Done (verified, with tests)** | Nothing |
| 04 | Profile / Photos / Q&A | ✅ **Done** | Q&A public attribution — **FIXED 2026-06-21** |
| 05 | Donations & Dashboard | 🟡 **2 code gaps + config** | "Donate anonymously by default" setting; per-type push gating (both code). Plus NGO config |
| 07 | Community (feed/reviews) | ✅ **Done (verified)** | Nothing |
| 08 | Gamification | ✅ **Done (verified)** | Nothing in backend scope |
| 09 | Settings & Accessibility | ❌ **Not zero-work** | 30-day deletion **purge** and **data export** both under-deliver vs the PRD's own copy |

---

## 🔴 Tier 1 — compliance / product promises that are broken

### #1 — PRD 09: 30-day account-deletion purge does not exist
`DELETE /users/me` only flips `is_deleted` + `deletion_requested_at` (`user_service.delete_me`,
`user_profile_repository.py:34`). **No scheduler job** ever consumes `deletion_requested_at` to
hard-delete, and the user's reviews / photos / Q&A / check-ins / journal / goals / donations are
never removed or anonymized. The 202 response body and the settings UI copy promise "purged
within 30 days" and "reviews and photos removed." *(Independently corroborated by the PRD 08
audit, which noted the purge job is absent.)*
**Needs:** a scheduled purge job (`core/scheduler.py`) that, past the 30-day window, hard-deletes
or anonymizes across all user-linked tables (gamification tables key on bare `user_id`, so no FK
blocking).

### #2 — PRD 09: data export is near-empty
`GET /users/me/export` returns only profile + followed masjids (`UserDataExport`,
`schemas/user.py:30`). For the "PDPO data portability" / "download my data before deleting"
flow it omits donations, reviews, Q&A, submissions, check-ins, journal entries, goals, badges.
**Needs:** widen the export aggregation to include every user-linked model.

### #3 — PRD 05: "donate anonymously by default" setting is missing entirely
PRD 05 explicitly says it *fills* this reserved PRD 09 slot, but there is no field on
`UserProfile`, no schema, no route (grep for `anonymous_by_default`/`donate_anon` → nothing).
The per-donation `is_anonymous` toggle works but defaults to `False` with no stored preference
to seed it from.
**Needs:** a `donate_anonymously_by_default` bool on `UserProfile` (+ migration), exposed via
the profile/notification-preferences endpoints, used to seed `DonationCreate.is_anonymous`.

### #4 — Per-message-type notification gating doesn't exist (PRD 05 US 53 + PRD 09 US 28)
*Flagged independently by both the PRD 05 and PRD 09 audits.* Notification controls today are
only `digest_hour` + per-masjid `notification_mode` (instant/digest/mute), which govern masjid
announcements/digests. `PushService.notify_users` (`push_service.py:131-140`) dispatches with
**no per-type preference check**, so a user cannot mute donation pushes (confirmed/recovery/
nudge/milestone) or the settings "Other" toggles (Eid / submission / moderation outcomes) —
both PRDs assert "nothing notifies me without a switch behind it."
**Needs:** per-message-type opt-out preferences + a check in the push fan-out.

---

## 🟠 Tier 2 — smaller functional gaps

### #5 — PRD 02: submission photo upload has no backend path
The submission model/schema accept a `photo_key` (`models/masjid_submission.py:56`,
`schemas/masjid_submission.py:16`) but there is **no presign endpoint** (`StorageService` only
does server-side `upload(bytes)`/`delete`) and **no multipart route** on the submissions router —
unlike community/masjid photos, which *do* take `UploadFile`. So "optionally attach a photo"
(PRD 02 lines 92, 127) is not deliverable, and nothing resolves a `photo_key` back to a URL for
the admin review queue.
**Needs:** either a presigned-URL endpoint or a multipart submission-photo upload route.

### #6 — PRD 04: public Q&A answer attribution — ✅ **FIXED 2026-06-21**
`QuestionPublic` omitted `answer_author_role`, so the client couldn't render "answered by the
masjid" vs "the NGO" (US 38). The column was already stored and populated at answer time; this
pass added `answer_author_role: str | None` to `app/schemas/masjid_question.py` (no migration —
the column already exists on the model). **Done.**

### #7 — PRD 05: support-from-donation-detail (US 51) — PARTIAL
`SupportTicket` carries no donation/entity reference and `TicketCategory` is Bug / IncorrectData
/ FeatureRequest / Other only. A donor can open a generic ticket, but the "one tap with full
[donation] context" degrades to free-text. **Needs:** an optional donation-reference field on the
support ticket.

---

## 🟡 Tier 3 — process / minor

- **PRD 01:** the PRD's Testing Decisions section **commits** a backend OTP pytest suite
  (cooldown / caps / 5-attempt lockout / expiry / token+is_new_user / bootstrap-once). The
  implementation is complete and correct; the committed test file does **not** exist.
- **PRD 02:** no `Cache-Control`/`ETag` on `nearby`/`search` despite the PRD's "cacheable"
  language (arguably a CDN/edge concern).
- **PRD 03:** `POST /admin/broadcast-push` is a platform-wide action that writes no audit-log
  entry (the code comment itself flags this as a "reasonable follow-up"). Also worth a one-line
  confirm with mobile that "follow == eligible for TIME_CHANGE ping."
- **PRD 09:** `410 Gone` for a deleted account is enforced only on `/users/me*`, not globally —
  a soft-deleted user could still write via other routers (tangential; mobile drops to guest on
  the 202).

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
  feed. Gaps are #3/#4/#7 above, not the core.
- **PRD 04 profile/photos/Q&A — verified:** profile data, community-photo pipeline + moderation,
  Q&A subsystem, suggest-an-edit, shared 7-day moderation routing predicate.
- **PRD 01 auth — verified:** OTP request/verify, cooldown/caps/lockout/TTL, profile bootstrap,
  consumer role isolation, refresh/logout/profile/follow reuse.
