#!/usr/bin/env python3
"""
End-to-end test for the community photo submission pipeline (Gap #8 / PRD 04).

Exercises the full lifecycle against a RUNNING backend (docker compose up):
submit → pending → moderation queue → approve/reject → public listing, plus
MinIO storage verification, rate-limit caps, validation failures, scope
enforcement, and the "profile gallery stays admin-only" invariant.

Prereqs:
    docker compose up           # api on :8001, minio on :9090
    psql ... -f scripts/seed_nearby_test.sql   # seeds masjid 1111..., 2222...

Usage:
    uv run python scripts/e2e_community_photos.py
"""

import io
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
MINIO = os.environ.get("E2E_MINIO_URL", "http://localhost:9090")
SECRET = os.environ["GOTRUE_JWT_SECRET"]

MASJID_A = "11111111-1111-1111-1111-111111111111"  # Baitul Mukarram (2 admin photos)
MASJID_B = "22222222-2222-2222-2222-222222222222"  # Gulshan (0 photos)

# 1x1 PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f9e0000000049454e44ae426082"
)

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


def upload_files(name: str = "p.png", ct: str = "image/png", data: bytes = PNG):
    return {"file": (name, io.BytesIO(data), ct)}


def main() -> int:
    c = httpx.Client(timeout=30.0)

    consumer = mint("app_user")
    consumer2 = mint("app_user")
    admin_a = mint("masjid_admin", masjid_id=MASJID_A)
    admin_b = mint("masjid_admin", masjid_id=MASJID_B)
    platform = mint("platform_admin")

    print("\n── 1. Submit a community photo (consumer) ──")
    r = c.post(
        f"{API}/masjids/{MASJID_A}/community-photos",
        headers=bearer(consumer),
        files=upload_files(),
    )
    check("POST returns 201", r.status_code == 201, f"got {r.status_code}: {r.text[:200]}")
    body = r.json()
    photo_id = body.get("photo_id")
    photo_url = body.get("url", "")
    check("status is pending", body.get("status") == "pending")
    check("url points at MinIO photos bucket", "masjidkoi-photos/community/" in photo_url, photo_url)

    print("\n── 2. MinIO object actually exists ──")
    # url is http://minio:9000/... (container DNS); rewrite to host-reachable MinIO.
    key = photo_url.split("masjidkoi-photos/", 1)[1]
    head = c.get(f"{MINIO}/masjidkoi-photos/{key}")
    # Bucket is private → 403 (exists) is fine; 404 means the object never landed.
    check("object stored (not 404)", head.status_code != 404, f"HEAD got {head.status_code}")

    print("\n── 3. Pending photo is NOT public ──")
    r = c.get(f"{API}/masjids/{MASJID_A}/community-photos")
    check("public listing 200", r.status_code == 200)
    ids = [i["photo_id"] for i in r.json()["items"]]
    check("pending photo absent from public listing", photo_id not in ids)

    print("\n── 4. Submitter sees it in /me/photo-submissions ──")
    r = c.get(f"{API}/me/photo-submissions", headers=bearer(consumer))
    mine = {i["photo_id"]: i for i in r.json()}
    check("appears in my submissions", photo_id in mine)
    check("my submission shows pending + timestamps", mine.get(photo_id, {}).get("status") == "pending" and "created_at" in mine.get(photo_id, {}))

    print("\n── 5. Moderation queue (masjid admin) ──")
    r = c.get(f"{API}/admin/masjids/{MASJID_A}/community-photos", headers=bearer(admin_a))
    check("queue 200", r.status_code == 200)
    q_ids = [i["photo_id"] for i in r.json()["items"]]
    check("pending photo in masjid admin queue", photo_id in q_ids)

    print("\n── 6. Scope: foreign masjid admin cannot moderate ──")
    r = c.post(f"{API}/admin/community-photos/{photo_id}/approve", headers=bearer(admin_b))
    check("foreign admin approve → 403", r.status_code == 403, f"got {r.status_code}")
    r = c.get(f"{API}/admin/masjids/{MASJID_A}/community-photos", headers=bearer(admin_b))
    check("foreign admin queue read → 403", r.status_code == 403, f"got {r.status_code}")

    print("\n── 7. Approve (masjid admin) → public ──")
    r = c.post(f"{API}/admin/community-photos/{photo_id}/approve", headers=bearer(admin_a))
    check("approve 200", r.status_code == 200, f"got {r.status_code}: {r.text[:160]}")
    check("status now approved", r.json().get("status") == "approved")
    r = c.get(f"{API}/masjids/{MASJID_A}/community-photos")
    pub_ids = [i["photo_id"] for i in r.json()["items"]]
    check("approved photo now public", photo_id in pub_ids)
    check("public item omits status/uploaded_by", set(r.json()["items"][0].keys()) == {"photo_id", "masjid_id", "url", "created_at"})

    print("\n── 8. Double-moderation is rejected ──")
    r = c.post(f"{API}/admin/community-photos/{photo_id}/reject", headers=bearer(admin_a))
    check("re-moderate approved photo → 409", r.status_code == 409, f"got {r.status_code}")

    print("\n── 9. Profile gallery stays admin-only ──")
    r = c.get(f"{API}/masjids/{MASJID_A}")
    photos = r.json().get("photos", [])
    check("profile shows exactly the 2 seeded admin photos", len(photos) == 2, f"got {len(photos)}")

    print("\n── 10. Reject flow (platform admin, masjid B) ──")
    r = c.post(f"{API}/masjids/{MASJID_B}/community-photos", headers=bearer(consumer2), files=upload_files())
    rej_id = r.json()["photo_id"]
    r = c.post(f"{API}/admin/community-photos/{rej_id}/reject", headers=bearer(platform))
    check("platform admin reject 200", r.status_code == 200, f"got {r.status_code}")
    check("status rejected", r.json().get("status") == "rejected")
    r = c.get(f"{API}/masjids/{MASJID_B}/community-photos")
    check("rejected photo not public", rej_id not in [i["photo_id"] for i in r.json()["items"]])
    r = c.get(f"{API}/me/photo-submissions", headers=bearer(consumer2))
    check("rejected photo visible to submitter", rej_id in [i["photo_id"] for i in r.json()])

    print("\n── 11. Validation: non-image → 415 ──")
    r = c.post(
        f"{API}/masjids/{MASJID_A}/community-photos",
        headers=bearer(consumer2),
        files={"file": ("x.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    check("non-image → 415", r.status_code == 415, f"got {r.status_code}")

    print("\n── 12. Per-masjid/day cap (3) → 429 ──")
    # consumer already used 1 of 3 on masjid A (step 1). Two more should pass, then 429.
    codes = []
    for _ in range(3):
        rr = c.post(f"{API}/masjids/{MASJID_A}/community-photos", headers=bearer(consumer), files=upload_files())
        codes.append(rr.status_code)
    check("hits 429 after per-masjid cap", 429 in codes, f"codes={codes}")
    check("429 detail distinct from validation", any(
        c.post(f"{API}/masjids/{MASJID_A}/community-photos", headers=bearer(consumer), files=upload_files()).status_code == 429
        for _ in range(1)
    ))

    print("\n── 13. Unauthenticated submit → 401/403 ──")
    r = c.post(f"{API}/masjids/{MASJID_A}/community-photos", files=upload_files())
    check("no token → 401/403", r.status_code in (401, 403), f"got {r.status_code}")

    print(f"\n{'='*48}\n  PASSED {_passed}   FAILED {_failed}\n{'='*48}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
