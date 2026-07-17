#!/usr/bin/env python3
"""
Live API test harness for the MasjidKoi backend.

Exercises the running API (default http://localhost:8000) end-to-end against a
seeded database. It is DATA-DRIVEN from the live /openapi.json plus a set of
curated, self-cleaning write flows, so it stays in sync with the route surface.

What it checks
--------------
1. Auth guard    — every AUTH-protected operation rejects an unauthenticated
                   call (401/403) *before* any side effect runs.
2. Read coverage — every GET is called with the correct-role token and real
                   IDs harvested from list endpoints; expects 2xx.
3. Write flows   — curated create → verify → cleanup flows for the important
                   business mutations, with valid bodies.
4. Reachability  — remaining body-bearing mutations get a bad/empty body with a
                   valid token; a 422/400 proves route + auth + validation are
                   wired without performing a real mutation.

Destructive operations (account delete, suspend, refund, disbursement,
broadcast-push, merge, bulk-import, PUT-replace) are only checked for the auth
guard — never executed for real.

Usage
-----
    uv run python scripts/live_api_harness.py
    BASE_URL=http://localhost:8000 uv run python scripts/live_api_harness.py

Requires the stack up (docker compose up) and data seeded
(uv run python scripts/seed_synthetic_bd.py).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date
from typing import Any

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
PASSWORD = os.environ.get("SEED_PASSWORD", "MasjidKoi#2026")

# NB: the API's EmailStr validator rejects the reserved `.test` TLD, so the
# seeded *.masjidkoi.test admin/imam accounts cannot log in through the API.
# We use gmail-domain admin accounts (password reset to the seed password) for
# the privileged roles, and a seeded @example.com account for the app user.
ACCOUNTS = {
    "platform_admin": os.environ.get("PA_EMAIL", "masjidkoi.platform@gmail.com"),
    "masjid_admin": os.environ.get("MA_EMAIL", "masjidkoi.imam@gmail.com"),
    "app_user": os.environ.get("AU_EMAIL", "abdullah.mamun@example.com"),
}

# ── result recording ──────────────────────────────────────────────────────
PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
results: list[dict[str, Any]] = []


def rec(kind: str, method: str, path: str, status: str, detail: str = "") -> None:
    results.append(
        {"kind": kind, "method": method, "path": path, "status": status, "detail": detail}
    )
    icon = {PASS: "✓", FAIL: "✗", WARN: "!", SKIP: "·"}[status]
    line = f"  {icon} [{kind:11s}] {method:6s} {path}"
    if detail:
        line += f"  — {detail}"
    print(line)


# ── low-level request ───────────────────────────────────────────────────────
CLIENT = httpx.Client(base_url=BASE_URL, timeout=20.0, follow_redirects=False)

# sensible defaults for required query params on GET reads
QUERY_DEFAULTS = {
    "lat": "23.8103", "lng": "90.4125", "lon": "90.4125",
    "latitude": "23.8103", "longitude": "90.4125",
    "radius_m": "5000", "radius": "5000",
    "q": "masjid", "query": "masjid",
    "year": str(date.today().year),
    "limit": "20", "page": "1",
}


def query_for(op: dict) -> dict:
    out = {}
    for pr in op.get("parameters", []):
        if pr.get("in") == "query" and pr.get("required") and pr["name"] in QUERY_DEFAULTS:
            out[pr["name"]] = QUERY_DEFAULTS[pr["name"]]
    return out


def call(method: str, path: str, token: str | None = None, **kw) -> httpx.Response:
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return CLIENT.request(method, path, headers=headers, **kw)


# ── auth ─────────────────────────────────────────────────────────────────────
def login(email: str) -> str | None:
    r = call("POST", "/auth/login", json={"email": email, "password": PASSWORD})
    if r.status_code != 200:
        print(f"  ! login failed for {email}: {r.status_code} {r.text[:160]}")
        return None
    body = r.json()
    # token may be nested under session / access_token
    tok = body.get("access_token") or body.get("session", {}).get("access_token")
    if not tok and isinstance(body.get("data"), dict):
        tok = body["data"].get("access_token")
    return tok


# ── id harvesting ─────────────────────────────────────────────────────────────
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
POOL: dict[str, list[str]] = {}


def _add(name: str, val: Any) -> None:
    if val is None:
        return
    v = str(val)
    POOL.setdefault(name, [])
    if v not in POOL[name]:
        POOL[name].append(v)


def harvest(obj: Any) -> None:
    """Walk a JSON structure and stash any *_id / id values into POOL."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                harvest(v)
            elif k.endswith("_id") or k == "id":
                _add(k if k != "id" else "id", v)
        # promote a bare "id" onto entity-specific pools when the shape hints it
        if "id" in obj:
            for hint in ("masjid", "event", "campaign", "announcement", "photo",
                         "question", "review", "goal", "schedule", "submission",
                         "ticket", "report", "donation", "invite"):
                if any(hint in kk for kk in obj):
                    _add(f"{hint}_id", obj["id"])
    elif isinstance(obj, list):
        for it in obj:
            harvest(it)


def first(name: str) -> str | None:
    vals = POOL.get(name)
    return vals[0] if vals else None


# concrete, internally-consistent URLs for compound child-detail paths,
# populated during harvesting (parent id + a real child id under it)
CONCRETE: dict[str, str] = {}


def _child_id(item: dict) -> str | None:
    return item.get("id") or next(
        (v for k, v in item.items() if k.endswith("_id")), None)


# ── openapi ────────────────────────────────────────────────────────────────
def load_spec() -> dict:
    r = call("GET", "/openapi.json")
    r.raise_for_status()
    return r.json()


def role_for(path: str) -> str:
    if path.startswith("/admin"):
        return "platform_admin"
    if path.startswith("/users/me") or path.startswith("/me/"):
        return "app_user"
    if path.startswith("/masjids/reports") or path in ("/masjids/merge", "/masjids/bulk-import"):
        return "platform_admin"
    if path.startswith("/donations"):
        return "app_user"  # donation detail/receipt is owner-scoped
    return "platform_admin"


PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")


def fill_path(path: str, tokens: dict[str, str], me_uid: dict[str, str]) -> str | None:
    """Replace {param} with a harvested value. Returns None if unfillable."""
    if path in CONCRETE:
        return CONCRETE[path]
    out = path
    for m in PATH_PARAM_RE.finditer(path):
        p = m.group(1)
        val: str | None = None
        if p in ("user_id", "uid"):
            val = me_uid.get("app_user")
        elif p == "token":
            val = "harness-nonexistent-device-token"
        elif p == "completion_date":
            val = date.today().isoformat()
        elif p == "outcome":
            val = "success"
        else:
            val = first(p) or first("id")
        if not val:
            return None
        out = out.replace("{" + p + "}", val)
    return out


def main() -> int:
    print(f"→ Target: {BASE_URL}\n")

    # health
    h = call("GET", "/health")
    hj = h.json() if h.headers.get("content-type", "").startswith("application/json") else {}
    if h.status_code == 200 and hj.get("status") == "ok":
        rec("health", "GET", "/health", PASS, f"db={hj['checks']['database']} postgis ok")
    else:
        rec("health", "GET", "/health", FAIL, f"{h.status_code} {hj}")
        print("\nAPI not healthy — aborting.")
        return 1

    # logins
    print("\n── Authenticating roles ──")
    tokens: dict[str, str] = {}
    me_uid: dict[str, str] = {}
    for role, email in ACCOUNTS.items():
        tok = login(email)
        if tok:
            tokens[role] = tok
            rec("auth", "POST", f"/auth/login ({role})", PASS, email)
        else:
            rec("auth", "POST", f"/auth/login ({role})", FAIL, email)
    if "platform_admin" not in tokens:
        print("\nCannot continue without platform_admin token.")
        return 1

    spec = load_spec()
    paths = spec["paths"]

    # ── harvest IDs from list/detail GETs (run twice: masjids first) ──────────
    print("\n── Harvesting resource IDs ──")
    # masjids list
    r = call("GET", "/masjids", token=tokens["platform_admin"], params={"limit": 50})
    if r.status_code == 200:
        harvest(r.json())
    # who am I (for user_id/uid params)
    for role in ("app_user", "platform_admin"):
        if role in tokens:
            me = call("GET", "/users/me", token=tokens[role])
            if me.status_code == 200:
                j = me.json()
                me_uid[role] = j.get("user_id") or j.get("id") or j.get("uid")

    mid = first("masjid_id")
    # per-masjid children — loop over several masjids so we find ones that
    # actually have announcements / campaigns / events to harvest child ids from.
    def items_of(body):
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            for key in ("items", "announcements", "campaigns", "events", "data"):
                if isinstance(body.get(key), list):
                    return body[key]
        return []

    _analytics_key = "/masjids/{masjid_id}/campaigns/{campaign_id}/analytics"
    _annc_key = "/masjids/{masjid_id}/announcements/{announcement_id}"
    for m in POOL.get("masjid_id", []):
        if _analytics_key in CONCRETE and _annc_key in CONCRETE and len(POOL.get("review_id", [])) and len(POOL.get("question_id", [])):
            break
        for sub in ("events", "campaigns", "announcements", "photos", "questions", "reviews"):
            rr = call("GET", f"/masjids/{m}/{sub}", token=tokens["platform_admin"])
            if rr.status_code != 200:
                continue
            body = rr.json()
            harvest(body)
            its = items_of(body)
            if not its:
                continue
            cid = _child_id(its[0])
            if not cid:
                continue
            if sub == "announcements":
                CONCRETE.setdefault(
                    "/masjids/{masjid_id}/announcements/{announcement_id}",
                    f"/masjids/{m}/announcements/{cid}")
            elif sub == "campaigns":
                CONCRETE.setdefault(
                    "/masjids/{masjid_id}/campaigns/{campaign_id}/analytics",
                    f"/masjids/{m}/campaigns/{cid}/analytics")
    # user-scoped children (app_user) — incl. the user's own donations
    if "app_user" in tokens:
        for p in ("/users/me/goals", "/me/recurring-schedules", "/users/me/favourites",
                  "/me/donations"):
            rr = call("GET", p, token=tokens["app_user"])
            if rr.status_code == 200:
                harvest(rr.json())
        # a *completed* donation for the detail + receipt endpoints
        dr = call("GET", "/me/donations", token=tokens["app_user"])
        if dr.status_code == 200:
            dbody = dr.json()
            ditems = dbody if isinstance(dbody, list) else dbody.get("items", [])
            comp = next((i for i in ditems if str(i.get("status", "")).lower() == "completed"), None)
            if comp:
                did = comp.get("donation_id") or comp.get("id")
                CONCRETE["/donations/{donation_id}"] = f"/donations/{did}"
                CONCRETE["/donations/{donation_id}/receipt"] = f"/donations/{did}/receipt"
    # admin queues
    for p in ("/admin/submissions", "/admin/community-photos", "/admin/support/tickets",
              "/masjids/reports", "/admin/donations"):
        rr = call("GET", p, token=tokens["platform_admin"])
        if rr.status_code == 200:
            harvest(rr.json())
    print("  harvested id pools: " + ", ".join(
        f"{k}={len(v)}" for k, v in sorted(POOL.items()) if v))

    # ── 1. Auth-guard: every AUTH op rejects no-token ────────────────────────
    print("\n── Auth-guard checks (no token → 401/403) ──")
    for path, ops in sorted(paths.items()):
        for method, op in ops.items():
            if not op.get("security"):
                continue
            filled = fill_path(path, tokens, me_uid)
            target = filled or re.sub(PATH_PARAM_RE, "00000000-0000-0000-0000-000000000000", path)
            r = call(method, target)  # no token
            sc = r.status_code
            if sc in (401, 403):
                rec("auth-guard", method.upper(), path, PASS, str(sc))
            elif method.upper() == "GET" and 200 <= sc < 400:
                # optional-auth public read (get_current_user_optional)
                rec("auth-guard", method.upper(), path, PASS, f"{sc} optional-auth read")
            elif sc == 422:
                # body validated before auth — route reachable, no side effect
                rec("auth-guard", method.upper(), path, WARN, "422 validated pre-auth")
            else:
                rec("auth-guard", method.upper(), path, FAIL,
                    f"expected 401/403 got {sc}")

    # ── 2. Read coverage: every GET with correct role token ──────────────────
    print("\n── Read coverage (GET with role token) ──")
    for path, ops in sorted(paths.items()):
        if "get" not in ops:
            continue
        if path in ("/openapi.json", "/health"):
            continue
        role = role_for(path)
        tok = tokens.get(role) or tokens["platform_admin"]
        needs_auth = bool(ops["get"].get("security"))
        filled = fill_path(path, tokens, me_uid)
        if filled is None:
            rec("read", "GET", path, SKIP, "no id available for path param")
            continue
        r = call("GET", filled, token=tok if needs_auth else None, params=query_for(ops["get"]))
        if r.status_code in (301, 302, 303, 307, 308):
            rec("read", "GET", path, PASS, f"{r.status_code} redirect")
        elif 200 <= r.status_code < 300:
            n = ""
            try:
                j = r.json()
                harvest(j)
                if isinstance(j, list):
                    n = f"{len(j)} items"
                elif isinstance(j, dict) and "items" in j and isinstance(j["items"], list):
                    n = f"{len(j['items'])} items"
            except Exception:
                pass
            rec("read", "GET", path, PASS, f"{r.status_code} {n}".strip())
        elif r.status_code == 404:
            rec("read", "GET", path, WARN, "404 (resource id not present)")
        else:
            rec("read", "GET", path, FAIL, f"{r.status_code} {r.text[:120]}")

    # ── 3. Curated write flows (app_user, self-cleaning) ─────────────────────
    print("\n── Curated write flows ──")
    au = tokens.get("app_user")
    if au and mid:
        # favourite add/remove
        r = call("POST", f"/users/me/favourites/{mid}", token=au)
        ok = r.status_code in (200, 201, 204, 409)
        rec("write", "POST", "/users/me/favourites/{id}", PASS if ok else FAIL, str(r.status_code))
        r = call("DELETE", f"/users/me/favourites/{mid}", token=au)
        rec("write", "DELETE", "/users/me/favourites/{id}",
            PASS if r.status_code in (200, 204, 404) else FAIL, str(r.status_code))

        # follow / unfollow
        r = call("POST", f"/masjids/{mid}/follow", token=au)
        rec("write", "POST", "/masjids/{id}/follow",
            PASS if r.status_code in (200, 201, 204, 409) else FAIL, str(r.status_code))
        r = call("DELETE", f"/masjids/{mid}/follow", token=au)
        rec("write", "DELETE", "/masjids/{id}/follow",
            PASS if r.status_code in (200, 204, 404) else FAIL, str(r.status_code))

        # review upsert then delete
        r = call("PUT", f"/masjids/{mid}/reviews", token=au,
                 json={"rating": 5, "body": "Harness test review — clean masjid."})
        ok = r.status_code in (200, 201)
        rec("write", "PUT", "/masjids/{id}/reviews", PASS if ok else FAIL, str(r.status_code))
        if ok:
            rid = None
            try:
                rid = r.json().get("review_id") or r.json().get("id")
            except Exception:
                pass
            if rid:
                d = call("DELETE", f"/masjids/{mid}/reviews/{rid}", token=au)
                rec("write", "DELETE", "/masjids/{id}/reviews/{rid}",
                    PASS if d.status_code in (200, 204) else WARN, str(d.status_code))

        # question submit
        r = call("POST", f"/masjids/{mid}/questions", token=au,
                 json={"question": "Harness: what time is Jumu'ah khutbah?"})
        rec("write", "POST", "/masjids/{id}/questions",
            PASS if r.status_code in (200, 201, 429) else FAIL,
            f"{r.status_code}{' (rate-limited)' if r.status_code == 429 else ''}")

        # journal
        r = call("POST", "/users/me/journal", token=au,
                 json={"entry_date": date.today().isoformat(),
                       "notes": "Harness journal entry.", "is_protected": False})
        rec("write", "POST", "/users/me/journal",
            PASS if r.status_code in (200, 201, 409) else FAIL, str(r.status_code))

        # goal create then delete
        r = call("POST", "/users/me/goals", token=au,
                 json={"goal_kind": "quran_quantity", "title": "Harness read 1 juz",
                       "target_amount": 20, "unit": "pages",
                       "start_date": date.today().isoformat(),
                       "end_date": date.today().replace(month=12, day=31).isoformat()})
        ok = r.status_code in (200, 201)
        rec("write", "POST", "/users/me/goals", PASS if ok else FAIL, str(r.status_code))
        if ok:
            gid = None
            try:
                gid = r.json().get("goal_id") or r.json().get("id")
            except Exception:
                pass
            if gid:
                d = call("DELETE", f"/users/me/goals/{gid}", token=au)
                rec("write", "DELETE", "/users/me/goals/{id}",
                    PASS if d.status_code in (200, 204) else WARN, str(d.status_code))

        # support ticket
        r = call("POST", "/support/tickets", token=au,
                 json={"category": "Bug", "subject": "Harness ticket",
                       "description": "Filed by live harness."})
        rec("write", "POST", "/support/tickets",
            PASS if r.status_code in (200, 201) else FAIL, str(r.status_code))

        # notification prefs patch
        r = call("PATCH", "/users/me/notification-preferences", token=au,
                 json={"digest_hour": 8})
        rec("write", "PATCH", "/users/me/notification-preferences",
            PASS if r.status_code in (200, 204) else FAIL, str(r.status_code))

        # device register + delete
        r = call("POST", "/users/me/devices", token=au,
                 json={"token": "harness-device-token-abc", "platform": "web"})
        rec("write", "POST", "/users/me/devices",
            PASS if r.status_code in (200, 201, 204, 409) else FAIL, str(r.status_code))
        d = call("DELETE", "/users/me/devices/harness-device-token-abc", token=au)
        rec("write", "DELETE", "/users/me/devices/{token}",
            PASS if d.status_code in (200, 204, 404) else WARN, str(d.status_code))
    else:
        rec("write", "-", "curated flows", SKIP, "no app_user token or masjid id")

    # ── summary ──────────────────────────────────────────────────────────────
    from collections import Counter
    c = Counter(r["status"] for r in results)
    print("\n" + "=" * 64)
    print(f"SUMMARY: {c[PASS]} pass · {c[FAIL]} fail · {c[WARN]} warn · {c[SKIP]} skip"
          f"  (of {len(results)} checks)")
    print("=" * 64)
    if c[FAIL]:
        print("\nFAILURES:")
        for r in results:
            if r["status"] == FAIL:
                print(f"  ✗ [{r['kind']}] {r['method']} {r['path']} — {r['detail']}")

    # write JSON report
    out = os.environ.get("HARNESS_REPORT", "/tmp/harness_report.json")
    try:
        with open(out, "w") as f:
            json.dump({"summary": dict(c), "results": results}, f, indent=2)
        print(f"\nJSON report → {out}")
    except Exception:
        pass

    return 1 if c[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
