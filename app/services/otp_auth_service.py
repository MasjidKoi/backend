"""
Consumer email-OTP authentication (passwordless).

GoTrue issues and validates the codes; this service layers MasjidKoi's policy on
top of GoTrue's native flow using the Redis layer:

  • 60s per-email resend cooldown
  • per-email and per-IP hourly send caps
  • 10-minute code validity
  • 5 wrong attempts before a fresh code is required
  • a fresh code invalidates the previous one's attempt budget

GoTrue intentionally returns the SAME error for a wrong code and an expired code
(anti-enumeration). To give the OTP screen its three distinct states
(wrong / expired / locked-out) we track an "issued" marker ourselves: if a verify
fails while a live code still exists it was wrong; if no live code exists it
expired.

Redis backs every abuse control here (lockout, send caps, per-IP verify cap,
wrong-vs-expired). Because those protections guard the most exposed auth surface,
the service FAILS CLOSED when Redis is unavailable — request/verify return 503
rather than silently skipping the caps and lockout (CODEBASE_AUDIT #6).
"""

import logging
from uuid import UUID

from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.user_profile_repository import UserProfileRepository
from app.schemas.auth import OtpRequestResponse, OtpTokenResponse
from app.services.gotrue_client import gotrue

logger = logging.getLogger(__name__)

# ── Policy (these numbers are mirrored verbatim by the mobile OTP screen) ──────
RESEND_COOLDOWN_S = 60
CODE_TTL_S = 600  # 10 minutes
MAX_VERIFY_ATTEMPTS = 5
PER_EMAIL_HOURLY_CAP = 5
PER_IP_HOURLY_CAP = 30
# Verify attempts from one source across ALL target emails per hour. The
# per-email lockout (5) only bounds a single target; this bounds a spray attack
# hammering /otp/verify against many emails from one IP.
PER_IP_VERIFY_HOURLY_CAP = 50
HOURLY_WINDOW_S = 3600


def _cooldown_key(email: str) -> str:
    return f"otp:cooldown:{email}"


def _issued_key(email: str) -> str:
    return f"otp:issued:{email}"


def _attempts_key(email: str) -> str:
    return f"otp:attempts:{email}"


def _email_cap_key(email: str) -> str:
    return f"otp:cap:email:{email}"


def _ip_cap_key(ip: str) -> str:
    return f"otp:cap:ip:{ip}"


def _ip_verify_key(ip: str) -> str:
    return f"otp:verify:ip:{ip}"


class OtpAuthService:
    def __init__(self, db: AsyncSession, redis: Redis | None) -> None:
        self.db = db
        self.redis = redis
        self.profiles = UserProfileRepository(db)

    # ── Request a code ─────────────────────────────────────────────────────────

    async def request_otp(self, email: str, client_ip: str) -> OtpRequestResponse:
        """
        Always returns 202 — never reveals whether the email maps to an account.

        When called inside the resend cooldown (or once a send cap is hit) no new
        code is sent; the response reports the seconds the client must wait.

        Fails closed with 503 when Redis is unavailable (the cooldown/send caps
        can't be enforced, so we refuse rather than allow an uncapped flood).
        """
        email = email.strip().lower()
        redis = self._require_redis()

        # Inside the resend window → don't send, just report time remaining.
        cooldown_ttl = await self._ttl(_cooldown_key(email))
        if cooldown_ttl > 0:
            return OtpRequestResponse(retry_after_seconds=cooldown_ttl)

        # Hourly abuse caps (per-email and per-IP). Still 202 to avoid leaking.
        capped_for = await self._check_send_caps(email, client_ip)
        if capped_for is not None:
            logger.warning("OTP send cap hit (email=%s ip=%s)", email, client_ip)
            return OtpRequestResponse(retry_after_seconds=capped_for)

        # Provision the account if needed, then let GoTrue email the code.
        await gotrue.ensure_consumer_user(email)
        await gotrue.send_email_otp(email)

        await redis.set(_cooldown_key(email), "1", ex=RESEND_COOLDOWN_S)
        await redis.set(_issued_key(email), "1", ex=CODE_TTL_S)
        # A fresh code resets the attempt budget for the old one.
        await redis.delete(_attempts_key(email))

        return OtpRequestResponse(retry_after_seconds=RESEND_COOLDOWN_S)

    # ── Verify a code ────────────────────────────────────────────────────────────

    async def verify_otp(
        self, email: str, code: str, client_ip: str
    ) -> OtpTokenResponse:
        email = email.strip().lower()
        redis = self._require_redis()

        # Per-IP verify throttle — bounds a spray attack hammering many target
        # emails from one source, which the per-email lockout alone cannot.
        ip_attempts = await self._incr_with_expiry(
            _ip_verify_key(client_ip), HOURLY_WINDOW_S
        )
        if ip_attempts > PER_IP_VERIFY_HOURLY_CAP:
            raise _too_many_attempts()

        # Hard per-email lockout before we ever ask GoTrue.
        attempts = int(await redis.get(_attempts_key(email)) or 0)
        if attempts >= MAX_VERIFY_ATTEMPTS:
            raise _too_many_attempts()

        session = await gotrue.verify_email_otp(email, code)
        if session is None:
            raise await self._classify_verify_failure(email)

        # Success — clear all OTP state for this email.
        await redis.delete(
            _cooldown_key(email), _issued_key(email), _attempts_key(email)
        )

        user_id = self._extract_user_id(session)
        is_new_user = await self._bootstrap_profile(user_id)

        return OtpTokenResponse(
            access_token=session["access_token"],
            token_type=session.get("token_type", "bearer"),
            expires_in=session["expires_in"],
            refresh_token=session["refresh_token"],
            is_new_user=is_new_user,
        )

    # ── Internals ────────────────────────────────────────────────────────────────

    def _require_redis(self) -> Redis:
        """Every OTP abuse control lives in Redis; when it is down we fail closed
        (503) rather than silently dropping the caps/lockout (CODEBASE_AUDIT #6)."""
        if self.redis is None:
            logger.error("OTP requested while Redis is unavailable — failing closed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "otp_unavailable"},
            )
        return self.redis

    async def _classify_verify_failure(self, email: str) -> HTTPException:
        """Resolve GoTrue's ambiguous rejection into wrong / expired / locked-out."""
        if self.redis is None:
            return HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "invalid_code"},
            )

        # No live code on record → it expired (or was already consumed).
        if not await self.redis.exists(_issued_key(email)):
            return HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "code_expired"},
            )

        # A live code exists → this was a wrong guess. Burn one attempt.
        attempts = await self.redis.incr(_attempts_key(email))
        if attempts == 1:
            await self.redis.expire(_attempts_key(email), CODE_TTL_S)

        remaining = MAX_VERIFY_ATTEMPTS - attempts
        if remaining <= 0:
            return _too_many_attempts()
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_code", "attempts_remaining": remaining},
        )

    async def _check_send_caps(self, email: str, client_ip: str) -> int | None:
        """
        Returns None if under both caps; otherwise the seconds until the breached
        window resets. Counts only when a send is actually about to happen.
        """
        email_count = await self._incr_with_expiry(
            _email_cap_key(email), HOURLY_WINDOW_S
        )
        if email_count > PER_EMAIL_HOURLY_CAP:
            return await self._ttl(_email_cap_key(email)) or HOURLY_WINDOW_S

        ip_count = await self._incr_with_expiry(_ip_cap_key(client_ip), HOURLY_WINDOW_S)
        if ip_count > PER_IP_HOURLY_CAP:
            return await self._ttl(_ip_cap_key(client_ip)) or HOURLY_WINDOW_S

        return None

    async def _incr_with_expiry(self, key: str, window_s: int) -> int:
        count = await self.redis.incr(key)  # type: ignore[union-attr]
        if count == 1:
            await self.redis.expire(key, window_s)  # type: ignore[union-attr]
        return count

    async def _ttl(self, key: str) -> int:
        ttl = await self.redis.ttl(key)  # type: ignore[union-attr]
        return ttl if ttl and ttl > 0 else 0

    def _extract_user_id(self, session: dict) -> UUID:
        raw = (session.get("user") or {}).get("id")
        if not raw:
            logger.error("GoTrue verify response missing user.id: %s", session.keys())
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Auth provider returned an unexpected response",
            )
        return UUID(raw)

    async def _bootstrap_profile(self, user_id: UUID) -> bool:
        """
        Create the user_profiles row on first-ever verify (all fields null; madhab
        is confirmed client-side later). Returns True iff this verify created it.
        """
        existing = await self.profiles.get_by_user_id(user_id)
        if existing is not None:
            return False
        await self.profiles.get_or_create(user_id, email=None)
        await self.profiles.commit()
        return True


def _too_many_attempts() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": "too_many_attempts"},
    )
