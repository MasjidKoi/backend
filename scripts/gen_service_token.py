#!/usr/bin/env python3
"""
Generate a GoTrue service_role JWT.

This token is used by FastAPI to call GoTrue admin endpoints
(create user, update app_metadata, ban user, etc.).

Run once and add the output to your .env as GOTRUE_SERVICE_ROLE_KEY.

Usage:
    uv run python scripts/gen_service_token.py

The GOTRUE_JWT_SECRET must be set in .env or passed via env:
    GOTRUE_JWT_SECRET=your-secret uv run python scripts/gen_service_token.py
"""

import os
import sys
import time

# Resolve project root so we can load .env
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    import jwt
except ImportError:
    print("Install PyJWT: uv add PyJWT")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass  # dotenv optional; rely on env vars

secret = os.environ.get("GOTRUE_JWT_SECRET")
if not secret:
    print("Error: GOTRUE_JWT_SECRET is not set.")
    print("Set it in .env or export it before running this script.")
    sys.exit(1)

now = int(time.time())
payload = {
    "role": "service_role",
    "iss": "supabase",
    "iat": now,
    # Service tokens are long-lived — rotate manually when secret changes
    "exp": now + 10 * 365 * 24 * 3600,  # 10 years
}

token = jwt.encode(payload, secret, algorithm="HS256")
print("GOTRUE_SERVICE_ROLE_KEY=" + token)
print()
print("Add the line above to your .env file.")
