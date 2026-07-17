#!/usr/bin/env python3
"""
Seed synthetic, Bangladesh-flavoured test data into MasjidKoi (Dhaka-weighted).

Fills EVERY application table with referentially-consistent, CHECK-constraint-
satisfying data so the API / admin panel can be exercised end to end.

What it does
------------
1. Creates loginable GoTrue users (1 platform admin, ~10 masjid-admin imams,
   ~25 app users) via the GoTrue admin API. Idempotent by email — re-running
   reuses existing accounts and refreshes their password + app_metadata.
2. Wipes its own previously-seeded rows (everything keyed off deterministic
   uuid5 ids, plus the user-scoped rows for the accounts above) and re-inserts
   a fresh, self-consistent snapshot inside one transaction.

All seeded users share the password below, so you can log in as any of them.

Usage
-----
    uv run python scripts/seed_synthetic_bd.py

Connects to Postgres directly on localhost:5432 (NOT pgbouncer) and GoTrue on
localhost:9999 — both confirmed reachable from the host. Override with env
SEED_DB_DSN / GOTRUE_EXTERNAL_URL if your ports differ.
"""

import asyncio
import json
import os
import random
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import asyncpg
import httpx

# ──────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────
DB_DSN = os.environ.get(
    "SEED_DB_DSN", "postgresql://masjidkoi:masjidkoi@localhost:5432/masjidkoi"
)
_gotrue = os.environ.get("GOTRUE_EXTERNAL_URL", "http://localhost:9999")
GOTRUE_URL = _gotrue.replace("http://gotrue:", "http://localhost:").rstrip("/")
SERVICE_KEY = os.environ.get("GOTRUE_SERVICE_ROLE_KEY", "")
ADMIN_HEADERS = {
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
    "apikey": SERVICE_KEY,
}
SEED_PASSWORD = "MasjidKoi#2026"

random.seed(1442)

# Deterministic id namespace so re-runs replace the same rows.
NS = uuid.UUID("00000000-0000-0000-0000-00000000bd01")
DHAKA_TZ = timezone(timedelta(hours=6))  # Asia/Dhaka
TODAY = date(2026, 6, 22)
NOW = datetime(2026, 6, 22, 10, 0, tzinfo=DHAKA_TZ)


def did(*parts) -> uuid.UUID:
    return uuid.uuid5(NS, ":".join(str(p) for p in parts))


def ts(days_ago=0, hour=10, minute=0):
    """A Dhaka-local timezone-aware timestamp, `days_ago` before TODAY."""
    d = TODAY - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=DHAKA_TZ)


def D(x) -> Decimal:
    return Decimal(str(x))


# ──────────────────────────────────────────────────────────────────────────
# People (romanised Bengali names)
# ──────────────────────────────────────────────────────────────────────────
APP_USERS = [
    # (slug, display_name, gender, madhab)
    ("abdullah.mamun", "Abdullah Al Mamun", "m", "hanafi"),
    ("rahim.uddin", "Mohammad Rahim Uddin", "m", "hanafi"),
    ("tanvir.ahmed", "Tanvir Ahmed", "m", "hanafi"),
    ("mahmudul.hasan", "Mahmudul Hasan", "m", "hanafi"),
    ("sakib.hossain", "Sakib Hossain", "m", "hanafi"),
    ("rafiqul.islam", "Rafiqul Islam", "m", "hanafi"),
    ("nazmul.huda", "Nazmul Huda", "m", "hanafi"),
    ("saiful.islam", "Saiful Islam", "m", "shafi"),
    ("ariful.haque", "Ariful Haque", "m", "hanafi"),
    ("kamrul.hasan", "Kamrul Hasan", "m", "hanafi"),
    ("mizanur.rahman", "Mizanur Rahman", "m", "hanafi"),
    ("shahidul.islam", "Shahidul Islam", "m", "hanafi"),
    ("habibur.rahman", "Habibur Rahman", "m", "hanafi"),
    ("jahangir.alam", "Jahangir Alam", "m", "hanafi"),
    ("imran.kabir", "Imran Kabir", "m", "hanafi"),
    ("fatema.akter", "Fatema Akter", "f", "hanafi"),
    ("ayesha.siddika", "Ayesha Siddika", "f", "hanafi"),
    ("nusrat.jahan", "Nusrat Jahan", "f", "hanafi"),
    ("sumaiya.islam", "Sumaiya Islam", "f", "hanafi"),
    ("tahmina.akter", "Tahmina Akter", "f", "hanafi"),
    ("rabeya.khatun", "Rabeya Khatun", "f", "hanafi"),
    ("marium.begum", "Marium Begum", "f", "hanafi"),
    ("sadia.afrin", "Sadia Afrin", "f", "shafi"),
    ("farzana.yasmin", "Farzana Yasmin", "f", "hanafi"),
    ("jannatul.ferdous", "Jannatul Ferdous", "f", "hanafi"),
]

IMAMS = [
    # (slug, display_name)
    ("imam.abdulqadir", "Mufti Abdul Qadir"),
    ("imam.ruhulamin", "Hafez Mufti Ruhul Amin"),
    ("imam.nurulislam", "Maulana Nurul Islam"),
    ("imam.mahbub", "Maulana Mahbubur Rahman"),
    ("imam.obaidullah", "Qari Obaidullah"),
    ("imam.ibrahim", "Mufti Ibrahim Khalil"),
    ("imam.delwar", "Maulana Delwar Hossain"),
    ("imam.shamsul", "Hafez Shamsul Haque"),
    ("imam.fazlul", "Maulana Fazlul Karim"),
    ("imam.yusuf", "Mufti Yusuf Ali"),
]


def bd_phone():
    op = random.choice(["17", "18", "19", "16", "15"])
    return "+8801" + op + "".join(random.choice("0123456789") for _ in range(7))


# ──────────────────────────────────────────────────────────────────────────
# Masjids — Dhaka-weighted (14 Dhaka + 6 elsewhere). status coverage included.
# (slug, name, address, region, lat, lng, status, verified, donations,
#  description, suspension_reason)
# ──────────────────────────────────────────────────────────────────────────
MASJIDS = [
    ("baitul-mukarram", "Baitul Mukarram National Mosque",
     "36 Topkhana Road, Paltan, Dhaka 1000", "Dhaka", 23.7297, 90.4136,
     "active", True, True,
     "The national mosque of Bangladesh, built in 1968. Holds up to 40,000 worshippers.",
     None),
    ("tara-masjid", "Tara Masjid (Star Mosque)",
     "Abul Khairat Road, Armanitola, Old Dhaka 1100", "Dhaka", 23.7186, 90.4039,
     "active", True, False,
     "A 19th-century mosque famous for its blue-star Chinitikri mosaic.", None),
    ("gulshan-azad", "Gulshan Azad Jame Masjid",
     "Road 50, Gulshan-2, Dhaka 1212", "Dhaka", 23.7925, 90.4148,
     "active", True, True, "Large community mosque in Gulshan-2.", None),
    ("dhanmondi-sobhanbag", "Dhanmondi Sobhanbag Jame Masjid",
     "Mirpur Road, Sobhanbag, Dhanmondi, Dhaka 1207", "Dhaka", 23.7546, 90.3736,
     "active", True, True, "Busy roadside mosque serving the Dhanmondi area.", None),
    ("uttara-azampur", "Uttara Azampur Jame Masjid",
     "Sonargaon Janapath Road, Sector 7, Uttara, Dhaka 1230", "Dhaka", 23.8687, 90.3984,
     "active", True, False, "Sector mosque in Uttara with a large sisters' section.", None),
    ("mirpur-dohs-central", "Mirpur DOHS Central Masjid",
     "Avenue 5, Mirpur DOHS, Dhaka 1216", "Dhaka", 23.8276, 90.3712,
     "active", True, True, "Residential-area central mosque in Mirpur DOHS.", None),
    ("banani-jame", "Banani Bidyaniketan Jame Masjid",
     "Road 11, Banani, Dhaka 1213", "Dhaka", 23.7936, 90.4043,
     "active", True, False, "Neighbourhood mosque near Banani 11.", None),
    ("mohammadpur-baitul-aman", "Mohammadpur Baitul Aman Jame Masjid",
     "Tajmahal Road, Mohammadpur, Dhaka 1207", "Dhaka", 23.7651, 90.3589,
     "active", True, True, "Well-known mosque on Tajmahal Road, Mohammadpur.", None),
    ("motijheel-arambagh", "Arambagh Jame Masjid",
     "Arambagh, Motijheel, Dhaka 1000", "Dhaka", 23.7331, 90.4189,
     "active", False, False, "Office-district mosque, very busy at Dhuhr.", None),
    ("bashundhara-jame", "Bashundhara R/A Jame Masjid",
     "Block C, Bashundhara R/A, Dhaka 1229", "Dhaka", 23.8190, 90.4377,
     "active", True, True, "Modern mosque inside Bashundhara Residential Area.", None),
    ("tejgaon-jame", "Tejgaon Central Jame Masjid",
     "Nabisco More, Tejgaon, Dhaka 1208", "Dhaka", 23.7644, 90.3937,
     "active", False, False, "Industrial-area mosque near Tejgaon.", None),
    ("lalbagh-shahi", "Lalbagh Shahi Jame Masjid",
     "Lalbagh Fort Road, Lalbagh, Dhaka 1211", "Dhaka", 23.7188, 90.3875,
     "active", True, False, "Historic Mughal-era mosque near Lalbagh Fort.", None),
    ("baridhara-new", "Baridhara New Jame Masjid",
     "Block J, Baridhara, Dhaka 1212", "Dhaka", 23.8049, 90.4254,
     "pending", False, False, "Community-submitted mosque awaiting verification.", None),
    ("savar-bazar", "Savar Bazar Jame Masjid",
     "Bazar Road, Savar, Dhaka 1340", "Dhaka", 23.8583, 90.2667,
     "suspended", False, False, "Temporarily suspended pending an ownership dispute.",
     "Suspended after multiple unresolved data-accuracy reports."),
    # ── Other divisions (breadth) ──
    ("andarkilla-shahi", "Andarkilla Shahi Jame Masjid",
     "Andarkilla, Kotwali, Chattogram 4000", "Chittagong", 23.3340, 91.8350,
     "active", True, False, "Mughal-conquest monument on the Andarkilla hill.", None),
    ("chandanpura", "Chandanpura Boro Masjid",
     "Chandanpura, Chattogram 4000", "Chittagong", 22.3501, 91.8362,
     "active", True, True, "Landmark mosque on Chattogram's Chandanpura road.", None),
    ("shah-jalal-dargah", "Hazrat Shah Jalal Dargah Jame Masjid",
     "Dargah Gate, Sylhet 3100", "Sylhet", 24.9045, 90.8690,
     "active", True, True, "Mosque at the shrine of Hazrat Shah Jalal (RA).", None),
    ("shat-gombuj", "Shat Gombuj (Sixty Dome) Mosque",
     "Sundarghona, Bagerhat 9300", "Khulna", 22.6610, 89.7585,
     "active", True, False, "UNESCO World Heritage 15th-century mosque.", None),
    ("bagha-shahi", "Bagha Shahi Mosque",
     "Bagha, Rajshahi 6280", "Rajshahi", 24.1965, 88.8245,
     "active", True, False, "Terracotta Sultanate-era mosque in Bagha.", None),
    ("guthia-baitul-aman", "Baitul Aman Jame Masjid (Guthia)",
     "Guthia, Ujirpur, Barishal 8210", "Barisal", 22.7430, 90.2480,
     "active", True, True, "Sprawling white mosque complex in Guthia.", None),
]


def main_imams_for_masjids():
    """Assign one imam account to each of the first len(IMAMS) active masjids."""
    active = [m for m in MASJIDS if m[6] == "active"]
    return list(zip(IMAMS, active))


# ──────────────────────────────────────────────────────────────────────────
# GoTrue user provisioning
# ──────────────────────────────────────────────────────────────────────────
async def list_users(client):
    out, page = {}, 1
    while True:
        r = await client.get(
            f"{GOTRUE_URL}/admin/users",
            headers=ADMIN_HEADERS,
            params={"page": page, "per_page": 200},
        )
        r.raise_for_status()
        users = r.json().get("users", [])
        for u in users:
            if u.get("email"):
                out[u["email"].lower()] = u["id"]
        if len(users) < 200:
            break
        page += 1
    return out


async def ensure_user(client, existing, email, app_metadata, display_name):
    email_l = email.lower()
    if email_l in existing:
        uid = existing[email_l]
        await client.put(
            f"{GOTRUE_URL}/admin/users/{uid}",
            headers=ADMIN_HEADERS,
            json={
                "password": SEED_PASSWORD,
                "app_metadata": app_metadata,
                "user_metadata": {"display_name": display_name},
                "email_confirm": True,
            },
        )
        return uid
    r = await client.post(
        f"{GOTRUE_URL}/admin/users",
        headers=ADMIN_HEADERS,
        json={
            "email": email,
            "password": SEED_PASSWORD,
            "email_confirm": True,
            "app_metadata": app_metadata,
            "user_metadata": {"display_name": display_name},
        },
    )
    if not r.is_success:
        raise RuntimeError(f"create user {email} failed: {r.status_code} {r.text}")
    uid = r.json()["id"]
    existing[email_l] = uid
    return uid


async def provision_users():
    """Returns dict: app_user_ids[slug], imam_ids[slug], platform_admin_id, masjid_admin_of[masjid_slug]."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        existing = await list_users(client)

        platform_admin_id = await ensure_user(
            client, existing, "platform.admin@masjidkoi.test",
            {"role": "platform_admin", "masjid_id": None, "madrasha_id": None},
            "MasjidKoi Platform Admin",
        )

        # Imams -> masjid admins, each scoped to their masjid
        imam_ids, masjid_admin_of = {}, {}
        for (islug, iname), masjid in main_imams_for_masjids():
            mslug = masjid[0]
            mid = str(did("masjid", mslug))
            uid = await ensure_user(
                client, existing, f"{islug}@masjidkoi.test",
                {"role": "masjid_admin", "masjid_id": mid, "madrasha_id": None},
                iname,
            )
            imam_ids[islug] = uid
            masjid_admin_of[mslug] = uid

        app_user_ids = {}
        for slug, name, _g, _m in APP_USERS:
            uid = await ensure_user(
                client, existing, f"{slug}@example.com",
                {"role": "app_user", "masjid_id": None, "madrasha_id": None},
                name,
            )
            app_user_ids[slug] = uid

        return platform_admin_id, imam_ids, masjid_admin_of, app_user_ids


# ──────────────────────────────────────────────────────────────────────────
# Generic insert helper (supports geog + jsonb casts)
# ──────────────────────────────────────────────────────────────────────────
async def insert_rows(conn, table, rows, casts=None):
    if not rows:
        return 0
    casts = casts or {}
    cols = list(rows[0].keys())
    # asyncpg.executemany cannot infer a column's type when it is NULL in every
    # row of the batch — guard against that with a clear error.
    for c in cols:
        if c not in casts and all(r[c] is None for r in rows):
            raise ValueError(
                f"{table}.{c} is NULL in every row — pass an explicit cast "
                f'(e.g. casts={{"{c}": "text"}}) so asyncpg can infer the type.'
            )
    exprs = []
    for i, c in enumerate(cols, start=1):
        kind = casts.get(c)
        if kind == "geog":
            exprs.append(f"ST_GeographyFromText(${i})")
        elif kind:  # "jsonb", "text", "timestamptz", … — any SQL type
            exprs.append(f"${i}::{kind}")
        else:
            exprs.append(f"${i}")
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(exprs)})"
    values = [tuple(r[c] for c in cols) for r in rows]
    await conn.executemany(sql, values)
    return len(values)


# ──────────────────────────────────────────────────────────────────────────
# Prayer-time templates (Dhaka late-June, Karachi method / Hanafi)
# ──────────────────────────────────────────────────────────────────────────
def prayer_template(offset_min):
    base = {
        "fajr": (3, 48), "dhuhr": (12, 5), "asr": (16, 35),
        "maghrib": (18, 50), "isha": (20, 12),
    }
    iq = {"fajr": 20, "dhuhr": 10, "asr": 10, "maghrib": 5, "isha": 10}

    def mk(h, m, add=0):
        total = h * 60 + m + offset_min + add
        return time((total // 60) % 24, total % 60)

    azan = {k: mk(h, m) for k, (h, m) in base.items()}
    iqamah = {k: mk(base[k][0], base[k][1], iq[k]) for k in base}
    return azan, iqamah


# ──────────────────────────────────────────────────────────────────────────
# Seed everything inside one transaction
# ──────────────────────────────────────────────────────────────────────────
async def seed_database(conn, platform_admin_id, imam_ids, masjid_admin_of, app_user_ids):
    pa = uuid.UUID(platform_admin_id)
    pa_email = "platform.admin@masjidkoi.test"
    app_uids = {s: uuid.UUID(v) for s, v in app_user_ids.items()}
    app_list = list(app_uids.items())  # (slug, uuid)
    imam_uids = {s: uuid.UUID(v) for s, v in imam_ids.items()}
    admin_of = {s: uuid.UUID(v) for s, v in masjid_admin_of.items()}

    name_of = {slug: name for slug, name, *_ in APP_USERS}
    madhab_of = {slug: m for slug, _n, _g, m in APP_USERS}
    email_of = {slug: f"{slug}@example.com" for slug, *_ in APP_USERS}

    M = {m[0]: m for m in MASJIDS}
    masjid_ids = {slug: did("masjid", slug) for slug in M}
    all_masjid_ids = list(masjid_ids.values())
    all_user_ids = [pa] + list(imam_uids.values()) + list(app_uids.values())

    def admin_for(mslug):
        """Admin user + email for a masjid (its imam, else platform admin)."""
        if mslug in admin_of:
            uid = admin_of[mslug]
            # find imam slug -> email
            islug = next(s for s, v in imam_uids.items() if v == uid)
            return uid, f"{islug}@masjidkoi.test"
        return pa, pa_email

    # Pre-compute deterministic id pools for idempotent cleanup
    report_ids = [did("report", i) for i in range(14)]
    receipt_ids = [f"seedbd-receipt-{i:02d}" for i in range(6)]
    audit_ids = [did("audit", i) for i in range(22)]

    # ── 1. Cleanup (FK-safe order) ──────────────────────────────────────────
    await conn.execute("DELETE FROM support_tickets WHERE user_id = ANY($1::uuid[])", all_user_ids)
    await conn.execute("DELETE FROM donations WHERE masjid_id = ANY($1::uuid[])", all_masjid_ids)
    await conn.execute("DELETE FROM disbursements WHERE masjid_id = ANY($1::uuid[])", all_masjid_ids)
    await conn.execute("DELETE FROM recurring_schedules WHERE masjid_id = ANY($1::uuid[])", all_masjid_ids)
    await conn.execute("DELETE FROM masjid_reports WHERE report_id = ANY($1::uuid[])", report_ids)
    await conn.execute("DELETE FROM masjid_submissions WHERE user_id = ANY($1::uuid[])", all_user_ids)
    await conn.execute("DELETE FROM user_journal_entries WHERE user_id = ANY($1::uuid[])", all_user_ids)
    await conn.execute("DELETE FROM user_goals WHERE user_id = ANY($1::uuid[])", all_user_ids)
    await conn.execute("DELETE FROM user_badges WHERE user_id = ANY($1::uuid[])", all_user_ids)
    await conn.execute("DELETE FROM user_checkins WHERE user_id = ANY($1::uuid[])", all_user_ids)
    await conn.execute("DELETE FROM device_tokens WHERE user_id = ANY($1::uuid[])", all_user_ids)
    await conn.execute("DELETE FROM user_profiles WHERE user_id = ANY($1::uuid[])", all_user_ids)
    await conn.execute("DELETE FROM push_receipts WHERE receipt_id = ANY($1::text[])", receipt_ids)
    await conn.execute("DELETE FROM audit_logs WHERE log_id = ANY($1::uuid[])", audit_ids)
    await conn.execute("DELETE FROM masjids WHERE masjid_id = ANY($1::uuid[])", all_masjid_ids)

    counts = {}

    # ── 2. Masjids + 1:1 tables ─────────────────────────────────────────────
    masjid_rows = []
    for slug, m in M.items():
        (_s, name, addr, region, lat, lng, status, verified, dono, desc, susp) = m
        masjid_rows.append({
            "masjid_id": masjid_ids[slug], "name": name, "address": addr,
            "admin_region": region,
            "location": f"SRID=4326;POINT({lng} {lat})",
            "status": status, "verified": verified, "donations_enabled": dono,
            "timezone": "Asia/Dhaka", "description": desc, "suspension_reason": susp,
            "created_at": ts(120), "updated_at": ts(2),
        })
    counts["masjids"] = await insert_rows(conn, "masjids", masjid_rows, {"location": "geog"})

    fac_rows, contact_rows, jumah_rows = [], [], []
    imam_name_of = {islug: iname for islug, iname in IMAMS}
    for slug, m in M.items():
        if m[6] == "removed":
            continue
        a_uid, _ = admin_for(slug)
        islug = next((s for s, v in imam_uids.items() if v == a_uid), None)
        imam_name = imam_name_of.get(islug, "Maulana Abdul Karim")
        fac_rows.append({
            "masjid_id": masjid_ids[slug],
            "has_sisters_section": slug in ("uttara-azampur", "gulshan-azad", "bashundhara-jame", "guthia-baitul-aman", "dhanmondi-sobhanbag"),
            "has_wudu_area": True,
            "has_wudu_male": True,
            "has_wudu_female": slug in ("uttara-azampur", "gulshan-azad", "bashundhara-jame", "guthia-baitul-aman"),
            "has_wheelchair_access": slug in ("baitul-mukarram", "gulshan-azad", "bashundhara-jame"),
            "has_parking": slug in ("gulshan-azad", "bashundhara-jame", "mirpur-dohs-central", "guthia-baitul-aman"),
            "parking_capacity": 30 if slug in ("gulshan-azad", "bashundhara-jame") else None,
            "has_janazah": True,
            "has_school": slug in ("baitul-mukarram", "mohammadpur-baitul-aman", "shah-jalal-dargah"),
            "imam_name": imam_name,
            "imam_qualifications": "Dawra-e-Hadith (Takmil), Qira'at certified",
            "imam_languages": "Bangla, Arabic, Urdu",
            "capacity_male": random.choice([300, 500, 800, 1200, 2000]),
            "capacity_female": random.choice([0, 80, 120, 200]),
            "updated_at": ts(5),
        })
        contact_rows.append({
            "masjid_id": masjid_ids[slug],
            "phone": bd_phone(), "email": f"info.{slug.replace('-', '')}@masjidkoi.test",
            "whatsapp": bd_phone(),
            "website_url": None,
            "updated_at": ts(5),
        })
        jumah_rows.append({
            "masjid_id": masjid_ids[slug],
            "khutbah_1_azan": time(13, 0), "khutbah_1_start": time(13, 15),
            "khutbah_2_azan": time(14, 0) if slug in ("baitul-mukarram", "gulshan-azad") else None,
            "khutbah_2_start": time(14, 15) if slug in ("baitul-mukarram", "gulshan-azad") else None,
            "notes": "Jumu'ah khutbah in Bangla; first 10 minutes in Arabic.",
            "updated_at": ts(5),
        })
    counts["masjid_facilities"] = await insert_rows(conn, "masjid_facilities", fac_rows)
    counts["masjid_contact"] = await insert_rows(
        conn, "masjid_contact", contact_rows, {"website_url": "text"})
    counts["jumah_schedules"] = await insert_rows(conn, "jumah_schedules", jumah_rows)

    # ── 3. Photos (admin gallery + community submissions) ────────────────────
    photo_rows = []
    for slug, m in M.items():
        if m[6] == "removed":
            continue
        for i in range(random.randint(2, 3)):
            photo_rows.append({
                "photo_id": did("photo", slug, i), "masjid_id": masjid_ids[slug],
                "url": f"https://cdn.masjidkoi.test/masjids/{slug}/admin-{i}.jpg",
                "is_cover": i == 0, "display_order": i,
                "source": "admin", "status": "approved", "uploaded_by": admin_for(slug)[0],
                "created_at": ts(90 - i), "updated_at": ts(90 - i),
            })
    # community submissions (mixed moderation status)
    comm = [
        ("baitul-mukarram", "abdullah.mamun", "approved"),
        ("baitul-mukarram", "fatema.akter", "pending"),
        ("gulshan-azad", "tanvir.ahmed", "approved"),
        ("gulshan-azad", "nusrat.jahan", "rejected"),
        ("dhanmondi-sobhanbag", "sakib.hossain", "pending"),
        ("uttara-azampur", "sumaiya.islam", "approved"),
        ("mirpur-dohs-central", "rafiqul.islam", "pending"),
        ("tara-masjid", "ayesha.siddika", "approved"),
        ("mohammadpur-baitul-aman", "kamrul.hasan", "rejected"),
        ("bashundhara-jame", "sadia.afrin", "pending"),
        ("chandanpura", "saiful.islam", "approved"),
        ("shah-jalal-dargah", "marium.begum", "approved"),
    ]
    for i, (mslug, uslug, st) in enumerate(comm):
        photo_rows.append({
            "photo_id": did("cphoto", mslug, uslug), "masjid_id": masjid_ids[mslug],
            "url": f"https://cdn.masjidkoi.test/masjids/{mslug}/community-{i}.jpg",
            "is_cover": False, "display_order": 0,
            "source": "community", "status": st, "uploaded_by": app_uids[uslug],
            "created_at": ts(20 - (i % 18)), "updated_at": ts(15 - (i % 14)),
        })
    counts["masjid_photos"] = await insert_rows(conn, "masjid_photos", photo_rows)

    # ── 4. Prayer times (13 days × active masjids) ───────────────────────────
    pt_rows = []
    active_masjids = [s for s, m in M.items() if m[6] == "active"]
    for mi, slug in enumerate(active_masjids):
        azan, iqamah = prayer_template(offset_min=mi % 4 - 1)
        for d in range(-4, 9):  # 2026-06-18 .. 2026-06-30
            day = TODAY + timedelta(days=d)
            manual = d <= 0  # past days admin-confirmed iqamah
            pt_rows.append({
                "prayer_time_id": did("pt", slug, day.isoformat()),
                "masjid_id": masjid_ids[slug], "date": day,
                "fajr_azan": azan["fajr"], "dhuhr_azan": azan["dhuhr"],
                "asr_azan": azan["asr"], "maghrib_azan": azan["maghrib"],
                "isha_azan": azan["isha"],
                "fajr_iqamah": iqamah["fajr"] if manual else None,
                "dhuhr_iqamah": iqamah["dhuhr"] if manual else None,
                "asr_iqamah": iqamah["asr"] if manual else None,
                "maghrib_iqamah": iqamah["maghrib"] if manual else None,
                "isha_iqamah": iqamah["isha"] if manual else None,
                "is_manual": manual, "calculation_method": "KARACHI", "madhab": "hanafi",
                "created_at": ts(10), "updated_at": ts(1),
            })
    counts["prayer_times"] = await insert_rows(conn, "prayer_times", pt_rows)

    return {
        "counts": counts, "M": M, "masjid_ids": masjid_ids, "active_masjids": active_masjids,
        "pa": pa, "pa_email": pa_email, "app_uids": app_uids, "app_list": app_list,
        "imam_uids": imam_uids, "admin_of": admin_of, "admin_for": admin_for,
        "name_of": name_of, "madhab_of": madhab_of, "email_of": email_of,
        "imam_name_of": imam_name_of, "all_masjid_ids": all_masjid_ids,
        "all_user_ids": all_user_ids, "report_ids": report_ids,
        "receipt_ids": receipt_ids, "audit_ids": audit_ids,
    }


# ──────────────────────────────────────────────────────────────────────────
# Community + engagement tables
# ──────────────────────────────────────────────────────────────────────────
async def seed_community(conn, ctx):
    counts = ctx["counts"]
    M, masjid_ids = ctx["M"], ctx["masjid_ids"]
    app_uids, app_list = ctx["app_uids"], ctx["app_list"]
    name_of, email_of = ctx["name_of"], ctx["email_of"]
    admin_for, imam_name_of = ctx["admin_for"], ctx["imam_name_of"]
    imam_uids, pa, pa_email = ctx["imam_uids"], ctx["pa"], ctx["pa_email"]
    active = ctx["active_masjids"]

    # ── Events + RSVPs ──────────────────────────────────────────────────────
    event_titles = [
        ("Tafsir-ul-Quran Mahfil", "Monthly tafsir gathering after Maghrib.", True, 200),
        ("Seerah Discussion Circle", "Weekly halaqa on the Prophetic biography.", True, 60),
        ("Hifz Graduation Ceremony", "Celebrating this year's huffaz.", True, 300),
        ("Free Eye Camp", "Community health camp in the masjid courtyard.", False, None),
        ("Ramadan Iftar Distribution", "Daily iftar for travellers and the needy.", False, None),
        ("Islamic Quiz for Children", "Fun Qur'an & Seerah quiz for kids.", True, 80),
    ]
    event_rows, rsvp_rows = [], []
    ev_masjids = active[:10]
    for mi, slug in enumerate(ev_masjids):
        a_uid, a_email = admin_for(slug)
        for j in range(random.randint(2, 3)):
            title, desc, rsvp, cap = event_titles[(mi + j) % len(event_titles)]
            offset = random.choice([-20, -7, 5, 12, 21])  # past + future
            eid = did("event", slug, j)
            event_rows.append({
                "event_id": eid, "masjid_id": masjid_ids[slug],
                "title": title, "description": desc,
                "event_date": TODAY + timedelta(days=offset),
                "event_time": time(random.choice([10, 16, 19, 20]), random.choice([0, 30])),
                "location": f"{M[slug][1]} — main prayer hall",
                "capacity": cap, "rsvp_enabled": rsvp,
                "created_by_id": a_uid, "created_by_email": a_email,
                "created_at": ts(30), "updated_at": ts(3),
            })
            if rsvp:
                for uslug, uid in random.sample(app_list, random.randint(3, 8)):
                    rsvp_rows.append({
                        "event_id": eid, "user_id": uid,
                        "rsvp_at": ts(random.randint(1, 15), random.randint(8, 21)),
                    })
    counts["masjid_events"] = await insert_rows(conn, "masjid_events", event_rows)
    counts["event_rsvps"] = await insert_rows(conn, "event_rsvps", rsvp_rows)

    # ── Announcements (published / scheduled / draft) ────────────────────────
    ann_templates = [
        ("Jumu'ah time update", "From this week the second Jumu'ah jamaat starts at 2:00 PM, insha'Allah."),
        ("Eid-ul-Adha jamaat schedule", "Eid jamaat at 7:00 AM and 8:00 AM in the masjid grounds."),
        ("Roof repair fund", "We are collecting for urgent roof repairs — please give generously."),
        ("New Hifz batch admission", "Admission open for the new Hifz batch. Contact the office."),
        ("Lost & found", "A set of keys was found after Asr. Collect from the muezzin."),
    ]
    ann_rows = []
    for mi, slug in enumerate(active[:11]):
        a_uid, a_email = admin_for(slug)
        for j in range(2):
            t, b = ann_templates[(mi + j) % len(ann_templates)]
            kind = ["published", "scheduled", "draft"][(mi + j) % 3]
            ann_rows.append({
                "announcement_id": did("ann", slug, j), "masjid_id": masjid_ids[slug],
                "title": t, "body": b,
                "is_published": kind == "published",
                "published_at": ts(j + 1) if kind == "published" else None,
                "posted_by_id": a_uid, "posted_by_email": a_email,
                "scheduled_at": ts(-3, 9) if kind == "scheduled" else None,
                "created_at": ts(j + 2), "updated_at": ts(j + 1),
            })
    counts["announcements"] = await insert_rows(conn, "announcements", ann_rows)

    # ── Reviews (unique per user+masjid) ─────────────────────────────────────
    bodies = [
        "Mashallah, very clean and spacious. Wudu area is well maintained.",
        "Imam sahib's recitation is beautiful. Parking is a bit tight on Fridays.",
        "Peaceful place for salah. Sisters' section could be larger.",
        "Air conditioning works well even in summer. Highly recommend.",
        "Good facilities but the iqamah times are sometimes inaccurate in the app.",
        "Excellent management and very welcoming community.",
        None, None,
    ]
    review_rows = []
    for slug in active:
        reviewers = random.sample(app_list, random.randint(3, 6))
        for uslug, uid in reviewers:
            rating = random.choices([5, 4, 3, 2], weights=[5, 4, 2, 1])[0]
            review_rows.append({
                "review_id": did("review", slug, uslug), "masjid_id": masjid_ids[slug],
                "user_id": uid, "rating": rating,
                "body": random.choice(bodies),
                "reviewer_display_name": name_of[uslug],
                "edited": random.random() < 0.15,
                "created_at": ts(random.randint(5, 80)), "updated_at": ts(random.randint(1, 4)),
            })
    counts["masjid_reviews"] = await insert_rows(conn, "masjid_reviews", review_rows)

    # ── Questions (pending / answered / rejected) ────────────────────────────
    qs = [
        "Is there a separate prayer space for women?",
        "What time does the Fajr jamaat start in winter?",
        "Do you offer Janazah prayer facilities?",
        "Is wheelchair access available at the main entrance?",
        "Are there Qur'an classes for children on weekends?",
        "Can I pay zakat through the masjid?",
        "Is parking available during Jumu'ah?",
        "Who is the current imam and what are his qualifications?",
    ]
    answers = {
        0: "Yes, alhamdulillah, there is a dedicated sisters' section on the first floor.",
        1: "Fajr jamaat is 15 minutes after azan throughout the year.",
        2: "Yes, we arrange Janazah prayer after any obligatory prayer on request.",
        3: "Yes, there is a ramp at the main gate for wheelchair access.",
    }
    q_rows = []
    qi = 0
    for slug in active[:8]:
        a_uid, a_email = admin_for(slug)
        for k in range(3):
            asker_slug, asker_uid = random.choice(app_list)
            status = ["answered", "pending", "rejected"][k % 3]
            idx = qi % len(qs)
            ans = answers.get(idx % len(qs)) if status == "answered" else None
            if status == "answered" and ans is None:
                ans = "Jazakallahu khairan for your question — yes, please contact the masjid office."
            q_rows.append({
                "question_id": did("q", slug, k), "masjid_id": masjid_ids[slug],
                "asker_user_id": asker_uid, "question": qs[idx],
                "status": status,
                "answer": ans,
                "answered_by": a_uid if status == "answered" else None,
                "answer_author_role": "masjid_admin" if status == "answered" else None,
                "answered_at": ts(random.randint(1, 10)) if status == "answered" else None,
                "created_at": ts(random.randint(10, 40)), "updated_at": ts(random.randint(1, 9)),
            })
            qi += 1
    counts["masjid_questions"] = await insert_rows(conn, "masjid_questions", q_rows)

    # ── Reports (data-accuracy) ──────────────────────────────────────────────
    report_specs = [
        ("dhuhr_iqamah", "Dhuhr iqamah is 5 minutes earlier than listed.", "pending"),
        ("contact.phone", "The phone number is no longer in service.", "reviewed"),
        ("address", "The mosque has moved to a new building next door.", "resolved"),
        ("facilities.has_parking", "There is actually no parking here.", "pending"),
        ("imam_name", "Imam has changed since last Ramadan.", "reviewed"),
        ("fajr_azan", "Fajr azan time looks off by a few minutes.", "pending"),
        ("name", "Spelling of the masjid name is incorrect.", "resolved"),
        ("facilities.has_wudu_female", "No female wudu area despite the listing.", "pending"),
        ("maghrib_iqamah", "Maghrib iqamah is immediately after azan, not +5.", "reviewed"),
        ("contact.email", "Email bounces back.", "pending"),
        ("description", "Capacity figure seems exaggerated.", "resolved"),
        ("website_url", "Website link is broken.", "pending"),
        ("asr_iqamah", "Asr iqamah differs from the board at the masjid.", "reviewed"),
        ("facilities.has_school", "There is no school attached to this masjid.", "pending"),
    ]
    report_rows = []
    for i, (field, desc, status) in enumerate(report_specs):
        slug = active[i % len(active)]
        is_guest = i % 4 == 3
        uslug, uid = random.choice(ctx["app_list"])
        report_rows.append({
            "report_id": ctx["report_ids"][i], "masjid_id": masjid_ids[slug],
            "user_id": None if is_guest else uid,
            "field_name": field, "description": desc,
            "reporter_email": "guest.reporter@example.com" if is_guest else email_of[uslug],
            "status": status,
            "created_at": ts(random.randint(2, 50)),
        })
    counts["masjid_reports"] = await insert_rows(conn, "masjid_reports", report_rows)

    # ── Follows (per-follow notification mode) ───────────────────────────────
    follow_rows = []
    for uslug, uid in app_list:
        for slug in random.sample(active, random.randint(2, 4)):
            follow_rows.append({
                "user_id": uid, "masjid_id": masjid_ids[slug],
                "followed_at": ts(random.randint(3, 90)),
                "notification_mode": random.choices(
                    ["digest", "instant", "mute"], weights=[6, 3, 1])[0],
            })
    counts["user_masjid_follows"] = await insert_rows(conn, "user_masjid_follows", follow_rows)

    # ── Co-admin invites ─────────────────────────────────────────────────────
    invite_specs = [
        ("baitul-mukarram", "abdullah.mamun", "Accepted"),
        ("gulshan-azad", "tanvir.ahmed", "Pending"),
        ("dhanmondi-sobhanbag", "newkhadem@example.com", "Pending"),
        ("uttara-azampur", "sakib.hossain", "Declined"),
        ("mirpur-dohs-central", "rafiqul.islam", "Accepted"),
        ("mohammadpur-baitul-aman", "assistant.imam@example.com", "Expired"),
        ("bashundhara-jame", "kamrul.hasan", "Revoked"),
        ("banani-jame", "nazmul.huda", "Pending"),
        ("chandanpura", "saiful.islam", "Accepted"),
        ("shah-jalal-dargah", "volunteer.sylhet@example.com", "Pending"),
    ]
    inv_rows = []
    for i, (slug, who, status) in enumerate(invite_specs):
        inviter, inviter_email = admin_for(slug)
        invited_email = email_of[who] if who in email_of else who
        gotrue_uid = app_uids[who] if (who in app_uids and status == "Accepted") else None
        future = status in ("Pending",)
        inv_rows.append({
            "invite_id": did("invite", slug, who), "masjid_id": masjid_ids[slug],
            "invited_email": invited_email,
            "invited_by_id": inviter, "invited_by_email": inviter_email,
            "gotrue_user_id": gotrue_uid, "status": status,
            "expires_at": ts(-7, 12) if future else ts(2, 12),
            "resend_count": random.randint(0, 2),
            "last_resent_at": ts(random.randint(1, 5)) if i % 3 == 0 else None,
            "created_at": ts(random.randint(5, 30)), "updated_at": ts(random.randint(1, 4)),
        })
    counts["masjid_co_admin_invites"] = await insert_rows(conn, "masjid_co_admin_invites", inv_rows)

    # ── Submissions (community "missing masjid") ─────────────────────────────
    sub_specs = [
        ("Kalabagan Maddhopara Jame Masjid", 23.7510, 90.3840, "Kalabagan, Dhaka", "pending", None),
        ("Rampura Bazar Jame Masjid", 23.7610, 90.4180, "Rampura, Dhaka", "pending", None),
        ("Mohakhali DOHS Masjid", 23.7780, 90.4030, "Mohakhali DOHS, Dhaka", "approved", "baridhara-new"),
        ("Khilgaon Taltola Jame Masjid", 23.7500, 90.4250, "Khilgaon, Dhaka", "pending", None),
        ("Shyamoli Square Masjid", 23.7740, 90.3650, "Shyamoli, Dhaka", "rejected", None),
        ("Jatrabari Boro Masjid", 23.7100, 90.4350, "Jatrabari, Dhaka", "pending", None),
        ("Agargaon IDB Masjid", 23.7780, 90.3790, "Agargaon, Dhaka", "approved", "baridhara-new"),
        ("Badda Link Road Masjid", 23.7840, 90.4260, "Badda, Dhaka", "pending", None),
        ("Narayanganj Chashara Jame Masjid", 23.6230, 90.4990, "Chashara, Narayanganj", "rejected", None),
        ("Gazipur Chowrasta Masjid", 23.9990, 90.4200, "Chowrasta, Gazipur", "pending", None),
    ]
    sub_rows = []
    for i, (name, lat, lng, addr, status, link) in enumerate(sub_specs):
        uslug, uid = app_list[i % len(app_list)]
        sub_rows.append({
            "submission_id": did("submission", i), "user_id": uid,
            "name": name, "latitude": lat, "longitude": lng, "address": addr,
            "photo_key": f"submissions/{did('submission', i)}.jpg" if i % 2 == 0 else None,
            "status": status,
            "approved_masjid_id": masjid_ids[link] if link else None,
            "created_at": ts(random.randint(5, 60)), "updated_at": ts(random.randint(1, 4)),
        })
    counts["masjid_submissions"] = await insert_rows(conn, "masjid_submissions", sub_rows)


# ──────────────────────────────────────────────────────────────────────────
# Finance + gamification + ops tables
# ──────────────────────────────────────────────────────────────────────────
def calc_fee(gross: Decimal) -> Decimal:
    # SSLCommerz-style ~1.85% + flat 5 BDT, rounded to 2dp
    return (gross * Decimal("0.0185")).quantize(Decimal("0.01")) + Decimal("5.00")


async def seed_finance(conn, ctx):
    counts = ctx["counts"]
    M, masjid_ids = ctx["M"], ctx["masjid_ids"]
    app_uids, app_list = ctx["app_uids"], ctx["app_list"]
    name_of, email_of, madhab_of = ctx["name_of"], ctx["email_of"], ctx["madhab_of"]
    imam_uids, pa, pa_email = ctx["imam_uids"], ctx["pa"], ctx["pa_email"]
    admin_for, imam_name_of = ctx["admin_for"], ctx["imam_name_of"]

    dono_masjids = [s for s, m in M.items() if m[6] == "active" and m[8]]  # donations_enabled

    # ── Campaigns ───────────────────────────────────────────────────────────
    camp_specs = [
        ("baitul-mukarram", "Masjid AC Renovation 2026", 1500000, "Active"),
        ("gulshan-azad", "New Sisters' Section Construction", 2500000, "Active"),
        ("dhanmondi-sobhanbag", "Winter Blanket Distribution", 500000, "Completed"),
        ("mirpur-dohs-central", "Boundary Wall Rebuild", 800000, "Active"),
        ("mohammadpur-baitul-aman", "Madrasah Library Fund", 600000, "Active"),
        ("bashundhara-jame", "Solar Panel Installation", 1200000, "Active"),
        ("chandanpura", "Minaret Repair Appeal", 900000, "Cancelled"),
        ("shah-jalal-dargah", "Musafir Khana Expansion", 2000000, "Active"),
        ("guthia-baitul-aman", "Eid Gift Fund for Orphans", 400000, "Completed"),
    ]
    campaigns = {}  # slug -> list of (campaign_id, target, status)
    camp_rows = []
    for mslug, title, target, status in camp_specs:
        cid = did("campaign", mslug, title)
        a_uid, a_email = admin_for(mslug)
        campaigns.setdefault(mslug, []).append((cid, Decimal(target), status))
        camp_rows.append({
            "campaign_id": cid, "masjid_id": masjid_ids[mslug], "title": title,
            "description": f"Help us with: {title}. May Allah accept your contribution.",
            "target_amount": Decimal(target), "raised_amount": Decimal("0.00"),
            "banner_url": f"https://cdn.masjidkoi.test/campaigns/{cid}.jpg",
            "start_date": TODAY - timedelta(days=60),
            "end_date": TODAY + timedelta(days=(60 if status == "Active" else -5)),
            "status": status, "created_by_id": a_uid, "created_by_email": a_email,
            "created_at": ts(60), "updated_at": ts(2),
        })
    counts["masjid_campaigns"] = await insert_rows(conn, "masjid_campaigns", camp_rows)

    # ── Donations (the ledger atom) ──────────────────────────────────────────
    pay_methods = ["bKash", "Nagad", "Rocket", "VISA-Brac Bank", "Mastercard-City Bank"]
    cats = ["general", "building", "zakat", "sadaqah", "lillah"]
    receipt_seq = 100000
    raised = {}  # campaign_id -> Decimal (completed only)
    completed_by_masjid = {}  # masjid_id -> Decimal net
    completed_donations = []  # (donation_id, masjid_slug, user_slug, gross)
    dono_rows = []
    n_don = 90
    for i in range(n_don):
        uslug, uid = app_list[i % len(app_list)]
        # 25% campaign donations (only for masjids that have campaigns)
        use_campaign = (i % 4 == 0) and campaigns
        if use_campaign:
            mslug = list(campaigns.keys())[i % len(campaigns)]
            cid, _t, _s = random.choice(campaigns[mslug])
            category = "campaign"
        else:
            mslug = dono_masjids[i % len(dono_masjids)]
            cid = None
            category = cats[i % len(cats)]
        gross = Decimal(random.choice(
            [100, 200, 500, 1000, 2000, 3000, 5000, 10000, 25000, 50000]))
        status = random.choices(
            ["completed", "pending", "failed", "refunded"],
            weights=[64, 14, 12, 10])[0]
        anon = random.random() < 0.2
        did_ = did("donation", i)
        created = ts(random.randint(1, 150), random.randint(8, 22), random.randint(0, 59))
        row = {
            "donation_id": did_, "user_id": uid, "masjid_id": masjid_ids[mslug],
            "campaign_id": cid, "category": category, "status": status,
            "gross_amount": gross, "fee_amount": Decimal("0.00"), "net_amount": gross,
            "is_anonymous": anon,
            "donor_name": name_of[uslug], "donor_email": email_of[uslug],
            "gateway_session_key": None, "gateway_val_id": None,
            "gateway_bank_tran_id": None, "gateway_payment_method": None,
            "receipt_number": None, "completed_at": None, "refunded_at": None,
            "created_at": created, "updated_at": created,
        }
        if status in ("completed", "refunded"):
            fee = calc_fee(gross)
            row["fee_amount"] = fee
            row["net_amount"] = gross - fee
            receipt_seq += 1
            row["receipt_number"] = f"MK-2026-{receipt_seq:06d}"
            row["gateway_val_id"] = f"VAL{did_.hex[:20]}"
            row["gateway_session_key"] = f"SESS{did_.hex[:24]}"
            row["gateway_bank_tran_id"] = f"BNK{did_.hex[:16].upper()}"
            row["gateway_payment_method"] = random.choice(pay_methods)
            comp = created + timedelta(minutes=random.randint(2, 30))
            row["completed_at"] = comp
            if status == "refunded":
                row["refunded_at"] = comp + timedelta(days=random.randint(1, 20))
            else:
                if cid is not None:
                    raised[cid] = raised.get(cid, Decimal(0)) + gross
                completed_by_masjid[mslug] = completed_by_masjid.get(mslug, Decimal(0)) + row["net_amount"]
                completed_donations.append((did_, mslug, uslug, gross))
        elif status == "pending":
            row["gateway_session_key"] = f"SESS{did_.hex[:24]}"
        dono_rows.append(row)
    counts["donations"] = await insert_rows(conn, "donations", dono_rows)

    # raised_amount per campaign (completed campaign donations only)
    for cid, amt in raised.items():
        if cid is not None:
            await conn.execute(
                "UPDATE masjid_campaigns SET raised_amount = $1 WHERE campaign_id = $2",
                amt, cid)

    # receipt counter (gapless per-year sequence — set to highest issued)
    await conn.execute(
        """INSERT INTO donation_receipt_counters (year, last_number)
           VALUES (2026, $1)
           ON CONFLICT (year) DO UPDATE
           SET last_number = GREATEST(donation_receipt_counters.last_number, EXCLUDED.last_number)""",
        receipt_seq)
    counts["donation_receipt_counters"] = 1

    # ── Disbursements (manual NGO payouts) ───────────────────────────────────
    disb_rows = []
    methods = ["bank", "bkash", "cash"]
    di = 0
    for mslug, net in completed_by_masjid.items():
        # pay out ~60% of the credited balance across 1-2 records
        payout_total = (net * Decimal("0.6")).quantize(Decimal("0.01"))
        if payout_total < Decimal("100"):
            continue
        n = random.randint(1, 2)
        each = (payout_total / n).quantize(Decimal("0.01"))
        for k in range(n):
            disb_rows.append({
                "disbursement_id": did("disb", mslug, k), "masjid_id": masjid_ids[mslug],
                "amount": each, "method": methods[di % 3],
                "reference": f"REF-{2026}{di:04d}",
                "disbursed_on": TODAY - timedelta(days=random.randint(3, 40)),
                "recorded_by_id": pa,
                "notes": f"Manual payout to {M[mslug][1]} via {methods[di % 3]}.",
                "created_at": ts(random.randint(3, 40)),
            })
            di += 1
    counts["disbursements"] = await insert_rows(conn, "disbursements", disb_rows)

    # ── Recurring schedules (reminder engine, never auto-charge) ─────────────
    rec_rows = []
    rec_freq = ["weekly", "monthly", "nightly"]
    rec_status = ["active", "active", "active", "paused", "cancelled"]
    for i in range(12):
        uslug, uid = app_list[i % len(app_list)]
        mslug = dono_masjids[i % len(dono_masjids)]
        freq = rec_freq[i % 3]
        status = rec_status[i % len(rec_status)]
        use_camp = (i % 5 == 0) and mslug in campaigns
        cid = random.choice(campaigns[mslug])[0] if use_camp else None
        category = "campaign" if use_camp else cats[i % len(cats)]
        amount = Decimal(random.choice([200, 500, 1000, 2000, 5000]))
        start = TODAY - timedelta(days=random.randint(20, 90))
        if freq == "nightly":
            end = TODAY + timedelta(days=10)  # last-10-nights style window
            step = timedelta(days=1)
        elif freq == "weekly":
            end = None
            step = timedelta(days=7)
        else:
            end = None
            step = timedelta(days=30)
        next_due = NOW + step if status == "active" else NOW - step
        rec_rows.append({
            "schedule_id": did("recurring", i), "user_id": uid,
            "masjid_id": masjid_ids[mslug], "campaign_id": cid,
            "category": category, "amount": amount, "frequency": freq,
            "start_date": start, "end_date": end, "next_due_at": next_due,
            "status": status, "created_at": ts(random.randint(20, 90)), "updated_at": ts(1),
        })
    counts["recurring_schedules"] = await insert_rows(conn, "recurring_schedules", rec_rows)

    # ── User profiles (all seeded accounts) ──────────────────────────────────
    prof_rows = []
    prof_rows.append({
        "user_id": pa, "display_name": "MasjidKoi Platform Admin", "madhab": "hanafi",
        "profile_photo_url": None, "is_deleted": False, "is_suspended": False,
        "suspended_at": None, "suspension_reason": None, "deletion_requested_at": None,
        "digest_hour": 19, "last_digest_sent_at": ts(1, 19),
        "donate_anonymously_by_default": False, "mute_donation_nudge": False,
        "mute_campaign_milestone": False, "mute_moderation_outcome": False,
        "mute_promotions": True, "purged_at": None,
        "created_at": ts(150), "updated_at": ts(1),
    })
    for islug, uid in imam_uids.items():
        prof_rows.append({
            "user_id": uid, "display_name": imam_name_of.get(islug, "Imam"),
            "madhab": "hanafi", "profile_photo_url": None, "is_deleted": False,
            "is_suspended": False, "suspended_at": None, "suspension_reason": None,
            "deletion_requested_at": None, "digest_hour": 5, "last_digest_sent_at": ts(1, 5),
            "donate_anonymously_by_default": False, "mute_donation_nudge": False,
            "mute_campaign_milestone": False, "mute_moderation_outcome": False,
            "mute_promotions": False, "purged_at": None,
            "created_at": ts(120), "updated_at": ts(1),
        })
    for idx, (uslug, uid) in enumerate(app_list):
        is_suspended = uslug == "jahangir.alam"
        is_deleted = uslug == "imran.kabir"
        prof_rows.append({
            "user_id": uid, "display_name": name_of[uslug], "madhab": madhab_of[uslug],
            "profile_photo_url": f"https://cdn.masjidkoi.test/avatars/{uslug}.jpg" if idx % 3 == 0 else None,
            "is_deleted": is_deleted, "is_suspended": is_suspended,
            "suspended_at": ts(10) if is_suspended else None,
            "suspension_reason": "Reported for spam reviews." if is_suspended else None,
            "deletion_requested_at": ts(5) if is_deleted else None,
            "digest_hour": random.randint(6, 21),
            "last_digest_sent_at": ts(1, random.randint(6, 21)) if idx % 2 == 0 else None,
            "donate_anonymously_by_default": idx % 5 == 0,
            "mute_donation_nudge": idx % 6 == 0, "mute_campaign_milestone": idx % 7 == 0,
            "mute_moderation_outcome": idx % 8 == 0, "mute_promotions": idx % 3 == 0,
            "purged_at": None,
            "created_at": ts(random.randint(30, 140)), "updated_at": ts(1),
        })
    counts["user_profiles"] = await insert_rows(
        conn, "user_profiles", prof_rows, {"purged_at": "timestamptz"})

    # ── Check-ins ────────────────────────────────────────────────────────────
    active = ctx["active_masjids"]
    checkin_rows = []
    ci = 0
    for uslug, uid in app_list:
        for _ in range(random.randint(1, 4)):
            slug = random.choice(active)
            checkin_rows.append({
                "checkin_id": did("checkin", uslug, ci),
                "user_id": uid, "masjid_id": masjid_ids[slug],
                "checked_in_at": ts(random.randint(0, 30), random.randint(4, 21), random.randint(0, 59)),
            })
            ci += 1
    counts["user_checkins"] = await insert_rows(conn, "user_checkins", checkin_rows)

    # ── Journal entries (ibadah streaks) ─────────────────────────────────────
    journal_rows = []
    journaling_users = app_list[:12]
    for uslug, uid in journaling_users:
        for d in range(1, 26):  # last 25 days
            day = TODAY - timedelta(days=d)
            miss = random.random() < 0.18
            protected = random.random() < 0.08
            prayed = not miss
            journal_rows.append({
                "journal_id": did("journal", uslug, day.isoformat()),
                "user_id": uid, "entry_date": day,
                "fajr": prayed or random.random() < 0.7,
                "dhuhr": prayed, "asr": prayed,
                "maghrib": prayed, "isha": prayed or random.random() < 0.8,
                "quran_amount": random.choice([1, 2, 3, 5, 0]) if not protected else None,
                "quran_unit": "pages" if not protected else None,
                "is_protected": protected,
                "notes": random.choice([None, None, "Alhamdulillah, good day.", "Travelling today."]),
                "created_at": ts(d), "updated_at": ts(d),
            })
    counts["user_journal_entries"] = await insert_rows(conn, "user_journal_entries", journal_rows)

    # ── Goals + completions ──────────────────────────────────────────────────
    goal_rows, completion_rows = [], []
    goal_users = app_list[:8]
    for gi, (uslug, uid) in enumerate(goal_users):
        # quran_quantity (khatm) goal
        qg_id = did("goal", uslug, "khatm")
        goal_rows.append({
            "goal_id": qg_id, "user_id": uid, "goal_kind": "quran_quantity",
            "template": "khatm_ramadan", "title": "Complete Qur'an in Ramadan",
            "status": "active" if gi % 4 else "paused",
            "target_amount": 604, "unit": "pages",
            "start_date": TODAY - timedelta(days=20), "end_date": TODAY + timedelta(days=10),
            "recurrence": None,
            "created_at": ts(20), "updated_at": ts(1),
        })
        # recurring daily goal (Ayat al-Kursi)
        dg_id = did("goal", uslug, "ayat")
        goal_rows.append({
            "goal_id": dg_id, "user_id": uid, "goal_kind": "recurring",
            "template": "ayat_al_kursi", "title": "Ayat al-Kursi after every salah",
            "status": "active", "target_amount": None, "unit": None,
            "start_date": None, "end_date": None, "recurrence": "daily",
            "created_at": ts(40), "updated_at": ts(1),
        })
        for d in range(1, random.randint(8, 22)):
            completion_rows.append({
                "completion_id": did("gc", uslug, "ayat", d),
                "goal_id": dg_id, "completion_date": TODAY - timedelta(days=d),
                "created_at": ts(d),
            })
        # recurring weekly goal (Surah al-Kahf) — only for some users
        if gi % 2 == 0:
            wg_id = did("goal", uslug, "kahf")
            goal_rows.append({
                "goal_id": wg_id, "user_id": uid, "goal_kind": "recurring",
                "template": "surah_al_kahf", "title": "Surah al-Kahf every Jumu'ah",
                "status": "active", "target_amount": None, "unit": None,
                "start_date": None, "end_date": None, "recurrence": "weekly",
                "created_at": ts(50), "updated_at": ts(1),
            })
            for w in range(1, 5):
                completion_rows.append({
                    "completion_id": did("gc", uslug, "kahf", w),
                    "goal_id": wg_id, "completion_date": TODAY - timedelta(days=7 * w + 1),
                    "created_at": ts(7 * w + 1),
                })
    counts["user_goals"] = await insert_rows(conn, "user_goals", goal_rows)
    counts["goal_completions"] = await insert_rows(conn, "goal_completions", completion_rows)

    # ── Badges (tiered milestones) ───────────────────────────────────────────
    badge_rows = []
    badge_plan = {
        "abdullah.mamun": [("FajrWarrior", [1, 2, 3]), ("GenerousGiver", [1, 2]), ("CommunityPillar", [1])],
        "rahim.uddin": [("FajrWarrior", [1, 2]), ("GenerousGiver", [1])],
        "tanvir.ahmed": [("GenerousGiver", [1, 2, 3])],
        "fatema.akter": [("FajrWarrior", [1]), ("CommunityPillar", [1, 2])],
        "ayesha.siddika": [("FajrWarrior", [1, 2])],
        "sakib.hossain": [("CommunityPillar", [1])],
        "nusrat.jahan": [("GenerousGiver", [1])],
        "mahmudul.hasan": [("FajrWarrior", [1, 2, 3]), ("CommunityPillar", [1])],
    }
    for uslug, plan in badge_plan.items():
        uid = app_uids[uslug]
        for btype, tiers in plan:
            for tier in tiers:
                badge_rows.append({
                    "badge_id": did("badge", uslug, btype, tier),
                    "user_id": uid, "badge_type": btype, "tier": tier,
                    "earned_at": ts(random.randint(2, 100)),
                })
    counts["user_badges"] = await insert_rows(conn, "user_badges", badge_rows)

    # ── Device tokens ────────────────────────────────────────────────────────
    dt_rows = []
    token_owners = app_list + [("imam.abdulqadir", imam_uids["imam.abdulqadir"]),
                               ("imam.ruhulamin", imam_uids["imam.ruhulamin"])]
    plats = ["ios", "android", "web"]
    for i, (uslug, uid) in enumerate(token_owners):
        for k in range(random.randint(1, 2)):
            dt_rows.append({
                "device_token_id": did("devtok", uslug, k),
                "token": f"ExponentPushToken[{did('devtok', uslug, k).hex[:22]}]",
                "user_id": uid, "platform": plats[(i + k) % 3],
                "created_at": ts(random.randint(5, 60)), "updated_at": ts(random.randint(0, 4)),
                "last_seen_at": ts(random.randint(0, 4), random.randint(6, 22)),
            })
    counts["device_tokens"] = await insert_rows(conn, "device_tokens", dt_rows)

    # ── Push receipts (transient delivery-receipt rows) ──────────────────────
    pr_rows = []
    for i, rid in enumerate(ctx["receipt_ids"]):
        pr_rows.append({
            "receipt_id": rid,
            "token": f"ExponentPushToken[{did('pushrcpt', i).hex[:22]}]",
            "created_at": ts(0, random.randint(0, 9), random.randint(0, 59)),
        })
    counts["push_receipts"] = await insert_rows(conn, "push_receipts", pr_rows)

    # ── Support tickets ──────────────────────────────────────────────────────
    completed_ids = [d[0] for d in completed_donations]
    ticket_specs = [
        ("Bug", "App crashes on prayer screen", "The app closes when I open prayer times for Gulshan masjid.", "Open"),
        ("IncorrectData", "Wrong Asr time", "Asr iqamah shown is 10 minutes early.", "InProgress"),
        ("FeatureRequest", "Add Qibla compass", "Please add a Qibla direction compass.", "Open"),
        ("DonationIssue", "Donation not confirmed", "I paid via bKash but got no receipt.", "InProgress"),
        ("Other", "How to follow a masjid?", "Cannot find the follow button.", "Resolved"),
        ("Bug", "Cannot upload photo", "Photo upload fails at 50%.", "Open"),
        ("DonationIssue", "Refund request", "Please refund my duplicate donation.", "InProgress"),
        ("IncorrectData", "Masjid name misspelled", "Name should be 'Baitul', not 'Baytul'.", "Resolved"),
        ("FeatureRequest", "Bangla language toggle", "Add full Bangla UI please.", "Open"),
        ("Other", "Account email change", "How do I change my registered email?", "Resolved"),
        ("Bug", "Notifications not arriving", "I enabled instant but get nothing.", "InProgress"),
        ("DonationIssue", "Receipt PDF blank", "Downloaded receipt PDF is empty.", "Open"),
    ]
    ticket_rows = []
    for i, (cat, subj, desc, status) in enumerate(ticket_specs):
        uslug, uid = app_list[i % len(app_list)]
        link = None
        if cat == "DonationIssue" and completed_ids:
            link = completed_ids[i % len(completed_ids)]
        assigned = pa if status in ("InProgress", "Resolved") else None
        ticket_rows.append({
            "ticket_id": did("ticket", i), "user_id": uid, "user_email": email_of[uslug],
            "category": cat, "subject": subj, "description": desc,
            "donation_id": link, "status": status,
            "assigned_to": assigned, "assigned_to_email": pa_email if assigned else None,
            "created_at": ts(random.randint(1, 40)), "updated_at": ts(random.randint(0, 4)),
        })
    counts["support_tickets"] = await insert_rows(conn, "support_tickets", ticket_rows)

    # ── Audit logs (append-only admin actions) ───────────────────────────────
    actions = [
        ("approve_submission", "masjid_submission", "Approved community submission"),
        ("create_masjid", "masjid", "Created masjid from submission"),
        ("update_prayer_times", "prayer_times", "Adjusted iqamah times"),
        ("create_campaign", "masjid_campaign", "Launched fundraising campaign"),
        ("record_disbursement", "disbursement", "Recorded manual payout"),
        ("suspend_masjid", "masjid", "Suspended pending dispute"),
        ("answer_question", "masjid_question", "Answered visitor question"),
        ("approve_photo", "masjid_photo", "Approved community photo"),
        ("reject_photo", "masjid_photo", "Rejected community photo"),
        ("resolve_report", "masjid_report", "Resolved data-accuracy report"),
        ("invite_co_admin", "masjid_co_admin_invite", "Invited co-admin"),
        ("update_platform_settings", "platform_settings", "Updated platform settings"),
        ("verify_masjid", "masjid", "Verified masjid profile"),
        ("publish_announcement", "announcement", "Published announcement"),
        ("reject_submission", "masjid_submission", "Rejected duplicate submission"),
        ("suspend_user", "user_profile", "Suspended user for spam"),
        ("refund_donation", "donation", "Initiated gateway refund"),
        ("update_facilities", "masjid_facilities", "Updated facility info"),
        ("create_event", "masjid_event", "Created masjid event"),
        ("enable_donations", "masjid", "Enabled donations for masjid"),
        ("update_jumah", "jumah_schedule", "Updated Jumu'ah schedule"),
        ("answer_question", "masjid_question", "Answered visitor question"),
    ]
    imam_list = list(imam_uids.items())
    audit_rows = []
    for i, log_id in enumerate(ctx["audit_ids"]):
        action, entity, note = actions[i % len(actions)]
        if i % 3 == 0:
            admin_id, admin_email, role = pa, pa_email, "platform_admin"
        else:
            islug, iuid = imam_list[i % len(imam_list)]
            admin_id, admin_email, role = iuid, f"{islug}@masjidkoi.test", "masjid_admin"
        audit_rows.append({
            "log_id": log_id, "admin_id": admin_id, "admin_email": admin_email,
            "admin_role": role, "action": action, "target_entity": entity,
            "target_id": did("audit-target", i),
            "ip_address": f"103.{random.randint(1, 250)}.{random.randint(1, 250)}.{random.randint(1, 250)}",
            "details": json.dumps({"note": note, "seed": True}),
            "created_at": ts(random.randint(1, 60), random.randint(9, 18)),
        })
    counts["audit_logs"] = await insert_rows(conn, "audit_logs", audit_rows, {"details": "jsonb"})

    # ── Platform settings (single config row) ────────────────────────────────
    existing = await conn.fetchval("SELECT settings_id FROM platform_settings LIMIT 1")
    settings_vals = dict(
        default_madhab="hanafi", default_calc_method="KARACHI", hijri_offset_days=0,
        supported_countries=["BD"], reviews_enabled=True, checkins_enabled=True,
        tax_deductible_receipts_enabled=True, platform_name="MasjidKoi",
        maintenance_mode=False, maintenance_message=None,
        terms_of_service="By using MasjidKoi you agree to use the service respectfully and lawfully.",
        privacy_policy="We store only the data needed to run prayer times, donations and community features.",
        terms_version="1.0", updated_by_email=pa_email,
    )
    if existing:
        await conn.execute(
            """UPDATE platform_settings SET
                 default_madhab=$1, default_calc_method=$2, hijri_offset_days=$3,
                 supported_countries=$4, reviews_enabled=$5, checkins_enabled=$6,
                 tax_deductible_receipts_enabled=$7, platform_name=$8, maintenance_mode=$9,
                 maintenance_message=$10, terms_of_service=$11, privacy_policy=$12,
                 terms_version=$13, updated_by_email=$14, updated_at=now()
               WHERE settings_id=$15""",
            settings_vals["default_madhab"], settings_vals["default_calc_method"],
            settings_vals["hijri_offset_days"], settings_vals["supported_countries"],
            settings_vals["reviews_enabled"], settings_vals["checkins_enabled"],
            settings_vals["tax_deductible_receipts_enabled"], settings_vals["platform_name"],
            settings_vals["maintenance_mode"], settings_vals["maintenance_message"],
            settings_vals["terms_of_service"], settings_vals["privacy_policy"],
            settings_vals["terms_version"], settings_vals["updated_by_email"], existing)
    else:
        await insert_rows(conn, "platform_settings", [{"settings_id": did("settings"), **settings_vals}], casts={"maintenance_message": "text"})
    counts["platform_settings"] = 1


# ──────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────
async def main():
    if not SERVICE_KEY:
        print("ERROR: GOTRUE_SERVICE_ROLE_KEY not set in .env")
        sys.exit(1)

    print("→ Provisioning GoTrue users …")
    platform_admin_id, imam_ids, masjid_admin_of, app_user_ids = await provision_users()
    print(f"  platform admin: 1, imams: {len(imam_ids)}, app users: {len(app_user_ids)}")

    conn = await asyncpg.connect(DB_DSN)
    try:
        async with conn.transaction():
            ctx = await seed_database(
                conn, platform_admin_id, imam_ids, masjid_admin_of, app_user_ids)
            await seed_community(conn, ctx)
            await seed_finance(conn, ctx)
        print("\n✓ Seed committed. Row counts inserted this run:")
        for table in sorted(ctx["counts"]):
            print(f"    {table:30s} {ctx['counts'][table]:>5d}")
    finally:
        await conn.close()

    print(f"\nAll seeded accounts share password: {SEED_PASSWORD}")
    print("  platform admin : platform.admin@masjidkoi.test")
    print("  imams          : imam.<name>@masjidkoi.test")
    print("  app users      : <first.last>@example.com")


if __name__ == "__main__":
    asyncio.run(main())
