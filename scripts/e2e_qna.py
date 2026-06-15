#!/usr/bin/env python3
"""
End-to-end test for the Masjid Q&A subsystem (Gap #9 / PRD 04).

Exercises the full lifecycle against a RUNNING backend (docker compose up):
ask → pending → moderation queue → answer/reject → public answered-only listing,
plus scope enforcement, the answered-only public invariant, the asker's own
view, length validation, rate-limit caps, and the auth gate.

Prereqs:
    docker compose up           # api on :8001
    psql ... -f scripts/seed_nearby_test.sql   # seeds masjid 1111..., 2222...

Usage:
    uv run python scripts/e2e_qna.py
"""

import os
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import jwt  # noqa: E402

API = os.environ.get("E2E_API_URL", "http://localhost:8001")
SECRET = os.environ["GOTRUE_JWT_SECRET"]

MASJID_A = "11111111-1111-1111-1111-111111111111"  # Baitul Mukarram
MASJID_B = "22222222-2222-2222-2222-222222222222"  # Gulshan

Q = "What time does the Fajr jamaat start during winter?"

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


def mint(role: str, *, masjid_id: str | None = None, sub: str | None = None) -> str:
    now = int(time.time())
    app_meta: dict = {"role": role}
    if masjid_id:
        app_meta["masjid_id"] = masjid_id
    payload = {
        "aud": "authenticated",
        "exp": now + 3600,
        "iat": now,
        "sub": sub or str(uuid.uuid4()),
        "email": f"{role}-{uuid.uuid4().hex[:6]}@example.com",
        "app_metadata": app_meta,
        "role": "authenticated",
        "aal": "aal1",
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    c = httpx.Client(timeout=30.0)

    consumer = mint("app_user")
    consumer2 = mint("app_user")
    admin_a = mint("masjid_admin", masjid_id=MASJID_A)
    admin_b = mint("masjid_admin", masjid_id=MASJID_B)
    platform = mint("platform_admin")

    print("\n── 1. Ask a question (consumer) ──")
    r = c.post(
        f"{API}/masjids/{MASJID_A}/questions",
        headers=bearer(consumer),
        json={"question": Q},
    )
    check(
        "POST returns 201", r.status_code == 201, f"got {r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    qid = body.get("question_id")
    check("status is pending", body.get("status") == "pending")
    check("answer is null", body.get("answer") is None)
    check("question echoed back", body.get("question") == Q)

    print("\n── 2. Pending question is NOT public ──")
    r = c.get(f"{API}/masjids/{MASJID_A}/questions")
    check("public listing 200", r.status_code == 200)
    ids = [i["question_id"] for i in r.json()["items"]]
    check("pending question absent from public listing", qid not in ids)

    print("\n── 3. Asker sees it in /me/questions ──")
    r = c.get(f"{API}/me/questions", headers=bearer(consumer))
    mine = {i["question_id"]: i for i in r.json()}
    check("appears in my questions", qid in mine)
    check(
        "my question shows pending + timestamps",
        mine.get(qid, {}).get("status") == "pending"
        and "created_at" in mine.get(qid, {}),
    )

    print("\n── 4. Moderation queue (masjid admin) ──")
    r = c.get(f"{API}/admin/masjids/{MASJID_A}/questions", headers=bearer(admin_a))
    check("queue 200", r.status_code == 200)
    q_ids = [i["question_id"] for i in r.json()["items"]]
    check("pending question in masjid admin queue", qid in q_ids)
    check(
        "moderation item exposes asker identity",
        any(
            i["question_id"] == qid and "asker_user_id" in i for i in r.json()["items"]
        ),
    )

    print("\n── 5. Scope: foreign masjid admin cannot moderate ──")
    r = c.post(
        f"{API}/admin/questions/{qid}/answer",
        headers=bearer(admin_b),
        json={"answer": "nope"},
    )
    check("foreign admin answer → 403", r.status_code == 403, f"got {r.status_code}")
    r = c.get(f"{API}/admin/masjids/{MASJID_A}/questions", headers=bearer(admin_b))
    check(
        "foreign admin queue read → 403", r.status_code == 403, f"got {r.status_code}"
    )

    print("\n── 6. Answer (masjid admin) → public ──")
    answer_text = "Fajr jamaat is at 5:45 AM through the winter months."
    r = c.post(
        f"{API}/admin/questions/{qid}/answer",
        headers=bearer(admin_a),
        json={"answer": answer_text},
    )
    check("answer 200", r.status_code == 200, f"got {r.status_code}: {r.text[:160]}")
    ans = r.json()
    check("status now answered", ans.get("status") == "answered")
    check("answer text persisted", ans.get("answer") == answer_text)
    check("answered_at stamped", ans.get("answered_at") is not None)
    check(
        "answer_author_role = masjid_admin",
        ans.get("answer_author_role") == "masjid_admin",
    )

    r = c.get(f"{API}/masjids/{MASJID_A}/questions")
    items = r.json()["items"]
    pub = next((i for i in items if i["question_id"] == qid), None)
    check("answered question now public", pub is not None)
    check("public item carries the answer", pub and pub.get("answer") == answer_text)
    check(
        "public item omits asker/answerer identity",
        pub is not None
        and set(pub.keys())
        == {
            "question_id",
            "masjid_id",
            "question",
            "answer",
            "answered_at",
            "created_at",
        },
    )

    print("\n── 7. Double-moderation is rejected ──")
    r = c.post(f"{API}/admin/questions/{qid}/reject", headers=bearer(admin_a))
    check(
        "re-moderate answered question → 409",
        r.status_code == 409,
        f"got {r.status_code}",
    )

    print("\n── 8. Length validation: too-short question → 422 ──")
    r = c.post(
        f"{API}/masjids/{MASJID_A}/questions",
        headers=bearer(consumer2),
        json={"question": "hi?"},
    )
    check("too-short question → 422", r.status_code == 422, f"got {r.status_code}")

    print("\n── 9. Reject flow (platform admin, masjid B) ──")
    r = c.post(
        f"{API}/masjids/{MASJID_B}/questions",
        headers=bearer(consumer2),
        json={"question": "Is there a women's prayer section available here?"},
    )
    rej_id = r.json()["question_id"]
    r = c.post(f"{API}/admin/questions/{rej_id}/reject", headers=bearer(platform))
    check("platform admin reject 200", r.status_code == 200, f"got {r.status_code}")
    check("status rejected", r.json().get("status") == "rejected")
    r = c.get(f"{API}/masjids/{MASJID_B}/questions")
    check(
        "rejected question not public",
        rej_id not in [i["question_id"] for i in r.json()["items"]],
    )
    r = c.get(f"{API}/me/questions", headers=bearer(consumer2))
    check(
        "rejected question visible to asker",
        rej_id in [i["question_id"] for i in r.json()],
    )

    print("\n── 10. Per-masjid/day cap (3) → 429 ──")
    # consumer already used 1 of 3 on masjid A (step 1). Two more pass, then 429.
    codes = []
    for i in range(3):
        rr = c.post(
            f"{API}/masjids/{MASJID_A}/questions",
            headers=bearer(consumer),
            json={
                "question": f"Cap-probe question number {i} about parking availability?"
            },
        )
        codes.append(rr.status_code)
    check("hits 429 after per-masjid cap", 429 in codes, f"codes={codes}")

    print("\n── 11. Unauthenticated ask → 401/403 ──")
    r = c.post(f"{API}/masjids/{MASJID_A}/questions", json={"question": Q})
    check("no token → 401/403", r.status_code in (401, 403), f"got {r.status_code}")

    print(f"\n{'=' * 48}\n  PASSED {_passed}   FAILED {_failed}\n{'=' * 48}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
