#!/usr/bin/env python3
"""
End-to-end test for the shared moderation-routing predicate (Gap #10 / PRD 04).

Two layers:

  A. PURE predicate — route_pending_item / can_moderate are exercised directly
     (no backend, no clock): claimed/unclaimed × fresh/overdue.

  B. INTEGRATION against a RUNNING backend, driven through the Q&A moderation
     endpoints (the community-photo service shares the identical code path):
       * a CLAIMED masjid routes fresh pending items to the masjid admin only;
         the NGO (platform admin) sees them only once they breach the 7-day SLA,
       * an UNCLAIMED masjid routes everything to the NGO immediately,
       * the masjid admin always sees & can act on their own masjid's items.

The claimed-admin signal (an Accepted co-admin invite) and a backdated
(>7-day-old) pending question are seeded directly via psycopg2 and cleaned up
afterwards so the other e2e suites are unaffected.

Prereqs:
    docker compose up                          # api on :8001, postgres on :5432
    psql ... -f scripts/seed_nearby_test.sql   # seeds masjid 1111..., 2222...

Usage:
    uv run python scripts/e2e_moderation_routing.py
"""

import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg2

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import jwt  # noqa: E402

from app.services.moderation_routing import (  # noqa: E402
    MODERATION_SLA,
    can_moderate,
    route_pending_item,
)

API = os.environ.get("E2E_API_URL", "http://localhost:8001")
DB_DSN = os.environ.get(
    "E2E_DB_DSN", "postgresql://masjidkoi:masjidkoi@localhost:5432/masjidkoi"
)
SECRET = os.environ["GOTRUE_JWT_SECRET"]

MASJID_CLAIMED = "11111111-1111-1111-1111-111111111111"  # Baitul Mukarram
MASJID_UNCLAIMED = "22222222-2222-2222-2222-222222222222"  # Gulshan

_passed = 0
_failed = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global _passed, _failed
    mark = "✅" if cond else "❌"
    print(f"  {mark} {name}" + (f"  — {extra}" if extra else ""))
    if cond:
        _passed += 1
    else:
        _failed += 1


def mint(role: str, *, masjid_id: str | None = None) -> str:
    now = int(time.time())
    app_meta: dict = {"role": role}
    if masjid_id:
        app_meta["masjid_id"] = masjid_id
    payload = {
        "aud": "authenticated",
        "exp": now + 3600,
        "iat": now,
        "sub": str(uuid.uuid4()),
        "email": f"{role}-{uuid.uuid4().hex[:6]}@example.com",
        "app_metadata": app_meta,
        "role": "authenticated",
        "aal": "aal1",
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── A. Pure predicate ────────────────────────────────────────────────────────


def test_pure() -> None:
    print("\n── A. Pure predicate ──")
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(days=1)
    overdue = now - (MODERATION_SLA + timedelta(hours=1))

    r = route_pending_item(masjid_has_claimed_admin=True, pending_since=fresh, now=now)
    check("claimed + fresh → masjid admin only", r.masjid_admin and not r.ngo)

    r = route_pending_item(
        masjid_has_claimed_admin=True, pending_since=overdue, now=now
    )
    check("claimed + overdue → masjid admin AND ngo (shared)", r.masjid_admin and r.ngo)

    r = route_pending_item(masjid_has_claimed_admin=False, pending_since=fresh, now=now)
    check("unclaimed + fresh → ngo only", r.ngo and not r.masjid_admin)

    m = uuid.uuid4()
    other = uuid.uuid4()
    # Platform admin (NGO) on a claimed masjid: blocked while fresh, allowed once overdue.
    check(
        "platform admin blocked on claimed+fresh",
        not can_moderate(
            is_platform_admin=True,
            user_masjid_id=None,
            item_masjid_id=m,
            masjid_has_claimed_admin=True,
            pending_since=fresh,
            now=now,
        ),
    )
    check(
        "platform admin allowed on claimed+overdue",
        can_moderate(
            is_platform_admin=True,
            user_masjid_id=None,
            item_masjid_id=m,
            masjid_has_claimed_admin=True,
            pending_since=overdue,
            now=now,
        ),
    )
    check(
        "platform admin allowed on unclaimed+fresh",
        can_moderate(
            is_platform_admin=True,
            user_masjid_id=None,
            item_masjid_id=m,
            masjid_has_claimed_admin=False,
            pending_since=fresh,
            now=now,
        ),
    )
    # Masjid admin: own masjid always, never restricted by the SLA; foreign never.
    check(
        "owner masjid admin allowed (claimed+fresh)",
        can_moderate(
            is_platform_admin=False,
            user_masjid_id=m,
            item_masjid_id=m,
            masjid_has_claimed_admin=True,
            pending_since=fresh,
            now=now,
        ),
    )
    check(
        "foreign masjid admin blocked",
        not can_moderate(
            is_platform_admin=False,
            user_masjid_id=other,
            item_masjid_id=m,
            masjid_has_claimed_admin=True,
            pending_since=overdue,
            now=now,
        ),
    )


# ── DB seeding ───────────────────────────────────────────────────────────────


def seed(conn) -> tuple[str, str, str]:
    """Returns (claim_invite_id, fresh_qid, overdue_qid)."""
    cur = conn.cursor()
    claim_id = str(uuid.uuid4())
    fresh_qid = str(uuid.uuid4())
    overdue_qid = str(uuid.uuid4())
    asker = str(uuid.uuid4())

    # Mark MASJID_CLAIMED as claimed via an Accepted co-admin invite.
    cur.execute(
        """
        INSERT INTO masjid_co_admin_invites
            (invite_id, masjid_id, invited_email, invited_by_id, status,
             expires_at, resend_count, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'Accepted', now() + interval '30 days', 0, now(), now())
        """,
        (claim_id, MASJID_CLAIMED, f"claimed-{uuid.uuid4().hex[:6]}@x.com", asker),
    )
    # A fresh pending question (routes to masjid admin only).
    cur.execute(
        """
        INSERT INTO masjid_questions
            (question_id, masjid_id, asker_user_id, question, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'pending', now(), now())
        """,
        (fresh_qid, MASJID_CLAIMED, asker, "Fresh question about jamaat timings here?"),
    )
    # A pending question backdated past the 7-day SLA (becomes NGO-visible).
    cur.execute(
        """
        INSERT INTO masjid_questions
            (question_id, masjid_id, asker_user_id, question, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'pending', now() - interval '8 days', now() - interval '8 days')
        """,
        (
            overdue_qid,
            MASJID_CLAIMED,
            asker,
            "Overdue question waiting more than a week?",
        ),
    )
    conn.commit()
    return claim_id, fresh_qid, overdue_qid


def cleanup(conn, claim_id, qids) -> None:
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM masjid_questions WHERE question_id = ANY(%s::uuid[])",
        (list(qids),),
    )
    cur.execute(
        "DELETE FROM masjid_co_admin_invites WHERE invite_id = %s::uuid", (claim_id,)
    )
    conn.commit()


# ── B. Integration ───────────────────────────────────────────────────────────


def test_integration(conn) -> None:
    c = httpx.Client(timeout=30.0)
    platform = mint("platform_admin")
    owner = mint("masjid_admin", masjid_id=MASJID_CLAIMED)

    claim_id, fresh_qid, overdue_qid = seed(conn)
    try:
        print("\n── B1. Claimed masjid: NGO queue shows overdue only ──")
        r = c.get(
            f"{API}/admin/masjids/{MASJID_CLAIMED}/questions?status=pending",
            headers=bearer(platform),
        )
        check("platform queue 200", r.status_code == 200, f"got {r.status_code}")
        ngo_ids = [i["question_id"] for i in r.json()["items"]]
        check("NGO sees the overdue pending item", overdue_qid in ngo_ids)
        check("NGO does NOT see the fresh pending item", fresh_qid not in ngo_ids)

        print("\n── B2. Claimed masjid: owner sees the full queue ──")
        r = c.get(
            f"{API}/admin/masjids/{MASJID_CLAIMED}/questions?status=pending",
            headers=bearer(owner),
        )
        owner_ids = [i["question_id"] for i in r.json()["items"]]
        check("owner sees the fresh item", fresh_qid in owner_ids)
        check("owner sees the overdue item", overdue_qid in owner_ids)

        print("\n── B3. NGO action gated by the SLA ──")
        r = c.post(
            f"{API}/admin/questions/{fresh_qid}/answer",
            headers=bearer(platform),
            json={"answer": "NGO trying a fresh claimed item."},
        )
        check(
            "platform answer on fresh claimed item → 403",
            r.status_code == 403,
            f"got {r.status_code}",
        )
        r = c.post(
            f"{API}/admin/questions/{overdue_qid}/answer",
            headers=bearer(platform),
            json={"answer": "NGO safety-net answer after the 7-day SLA."},
        )
        check(
            "platform answer on overdue claimed item → 200",
            r.status_code == 200,
            f"got {r.status_code}",
        )

        print("\n── B4. Owner can act on the fresh claimed item ──")
        r = c.post(
            f"{API}/admin/questions/{fresh_qid}/answer",
            headers=bearer(owner),
            json={"answer": "Owner answers their own masjid's fresh question."},
        )
        check(
            "owner answer on fresh item → 200",
            r.status_code == 200,
            f"got {r.status_code}",
        )

        print("\n── B5. Unclaimed masjid: NGO owns it immediately ──")
        consumer = mint("app_user")
        r = c.post(
            f"{API}/masjids/{MASJID_UNCLAIMED}/questions",
            headers=bearer(consumer),
            json={"question": "Fresh question at an unclaimed masjid right now?"},
        )
        unclaimed_qid = r.json()["question_id"]
        r = c.get(
            f"{API}/admin/masjids/{MASJID_UNCLAIMED}/questions?status=pending",
            headers=bearer(platform),
        )
        check(
            "NGO sees fresh item at unclaimed masjid",
            unclaimed_qid in [i["question_id"] for i in r.json()["items"]],
        )
        r = c.post(
            f"{API}/admin/questions/{unclaimed_qid}/answer",
            headers=bearer(platform),
            json={"answer": "NGO answers — no claimed admin to route to."},
        )
        check(
            "platform answer on unclaimed fresh item → 200",
            r.status_code == 200,
            f"got {r.status_code}",
        )
    finally:
        cleanup(conn, claim_id, [fresh_qid, overdue_qid])


def main() -> int:
    test_pure()
    conn = psycopg2.connect(DB_DSN)
    try:
        test_integration(conn)
    finally:
        conn.close()
    print(f"\n{'=' * 48}\n  PASSED {_passed}   FAILED {_failed}\n{'=' * 48}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
