#!/usr/bin/env python3
"""
Seed a platform admin account in GoTrue.

Usage:
    uv run python scripts/seed_platform_admin.py

Options (env vars or prompts):
    ADMIN_EMAIL     Platform admin email (default: prompts)
    ADMIN_PASSWORD  Password — if set, creates with password directly (no invite email)
                    If not set, sends invite email via Brevo

The script:
  1. Reads GOTRUE_SERVICE_ROLE_KEY + GOTRUE_URL from .env
  2. Creates (or updates) the user in GoTrue with role=platform_admin
  3. Either sets a direct password OR sends invite email

Run once on a fresh deployment. Safe to re-run — handles existing users.
"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import httpx

# Use GOTRUE_EXTERNAL_URL (public host URL) when running from outside Docker.
# Falls back to localhost:9999 if not set.
_gotrue_url = os.environ.get("GOTRUE_EXTERNAL_URL") or os.environ.get("GOTRUE_URL", "")
# Replace Docker-internal hostname with localhost for scripts running on the host
GOTRUE_URL = _gotrue_url.replace("http://gotrue:", "http://localhost:").rstrip("/") or "http://localhost:9999"
SERVICE_KEY = os.environ.get("GOTRUE_SERVICE_ROLE_KEY", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

ADMIN_HEADERS = {
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
    "apikey": SERVICE_KEY,
}

APP_METADATA = {
    "role": "platform_admin",
    "masjid_id": None,
    "madrasha_id": None,
}


def prompt(label: str, default: str = "") -> str:
    value = input(f"{label}{f' [{default}]' if default else ''}: ").strip()
    return value or default


async def find_user_by_email(email: str, client: httpx.AsyncClient) -> dict | None:
    resp = await client.get(f"{GOTRUE_URL}/admin/users", headers=ADMIN_HEADERS)
    if not resp.is_success:
        return None
    users = resp.json().get("users", [])
    return next((u for u in users if u.get("email") == email), None)


async def set_app_metadata(user_id: str, client: httpx.AsyncClient) -> None:
    resp = await client.put(
        f"{GOTRUE_URL}/admin/users/{user_id}",
        json={"app_metadata": APP_METADATA},
        headers=ADMIN_HEADERS,
    )
    resp.raise_for_status()


async def main() -> None:
    if not SERVICE_KEY:
        print("ERROR: GOTRUE_SERVICE_ROLE_KEY is not set in .env")
        print("Run:  uv run python scripts/gen_service_token.py")
        sys.exit(1)

    print("\n=== MasjidKoi — Seed Platform Admin ===\n")

    email = os.environ.get("ADMIN_EMAIL") or prompt("Email")
    password = os.environ.get("ADMIN_PASSWORD") or prompt("Password (leave blank to send invite email)")

    if not email:
        print("ERROR: email is required")
        sys.exit(1)

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:

        # Check if user already exists
        existing = await find_user_by_email(email, client)

        if existing:
            print(f"\n⚠  User '{email}' already exists (id: {existing['id']})")
            current_role = existing.get("app_metadata", {}).get("role")
            print(f"   Current role: {current_role or 'none'}")

            if current_role == "platform_admin":
                print("   Already a platform_admin — updating app_metadata just in case.")
                await set_app_metadata(existing["id"], client)
                print("   ✓ app_metadata confirmed.")

                if password:
                    # Update password
                    resp = await client.put(
                        f"{GOTRUE_URL}/admin/users/{existing['id']}",
                        json={"password": password},
                        headers=ADMIN_HEADERS,
                    )
                    resp.raise_for_status()
                    print("   ✓ Password updated.")
                print("\n✓ Done — platform_admin is ready.")
                return
            else:
                confirm = input(f"   Promote to platform_admin? [y/N]: ").strip().lower()
                if confirm != "y":
                    print("Aborted.")
                    sys.exit(0)
                await set_app_metadata(existing["id"], client)
                print("   ✓ Promoted to platform_admin.")
                print("\n✓ Done.")
                return

        # Create new user
        if password:
            # Direct creation with password — no invite email
            print(f"\nCreating platform_admin '{email}' with password...")
            resp = await client.post(
                f"{GOTRUE_URL}/admin/users",
                json={
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                    "app_metadata": APP_METADATA,
                },
                headers=ADMIN_HEADERS,
            )
            if not resp.is_success:
                print(f"ERROR: {resp.text}")
                sys.exit(1)
            user = resp.json()
            print(f"\n✓ Platform admin created!")
            print(f"  ID:    {user['id']}")
            print(f"  Email: {user['email']}")
            print(f"  Role:  {user.get('app_metadata', {}).get('role')}")
            print(f"\n  Login at: {FRONTEND_URL}/login")

        else:
            # Send invite email via POST /invite
            print(f"\nSending invite email to '{email}'...")
            resp = await client.post(
                f"{GOTRUE_URL}/invite",
                json={
                    "email": email,
                    "redirect_to": f"{FRONTEND_URL}/invite/accept",
                },
                headers=ADMIN_HEADERS,
            )
            if not resp.is_success:
                print(f"ERROR: {resp.text}")
                sys.exit(1)
            user = resp.json()

            # Set app_metadata (POST /invite doesn't support it)
            await set_app_metadata(user["id"], client)

            print(f"\n✓ Invite sent!")
            print(f"  ID:    {user['id']}")
            print(f"  Email: {email}")
            print(f"  Role:  platform_admin")
            print(f"\n  The admin will receive an email with a link to set their password.")
            print(f"  They will be directed to: {FRONTEND_URL}/invite/accept")

    print("\n=== Next steps ===")
    if password:
        print(f"  1. Open {FRONTEND_URL}/login")
        print(f"  2. Log in with {email} / [your password]")
        print(f"  3. You will land on /admin")
    else:
        print(f"  1. Check {email} inbox for the invite email")
        print(f"  2. Click the link → set a password")
        print(f"  3. Log in at {FRONTEND_URL}/login → /admin")


if __name__ == "__main__":
    asyncio.run(main())
