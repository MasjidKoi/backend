"""Committed backend test suite for the email-OTP auth service (PRD 01).

The PRD's Testing Decisions section commits these cases: resend cooldown
enforced + reported, per-email/per-IP send caps, the 5-attempt lockout, code
expiry, a successful verify returning tokens + is_new_user, and the profile row
bootstrapped exactly once.

Unit-style: the service is constructed directly with the real test `db` session
(so profile bootstrap actually persists) and a `FakeRedis`; the GoTrue singleton
is stubbed via monkeypatch.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.repositories.user_profile_repository import UserProfileRepository
from app.services import otp_auth_service as otp_mod
from app.services.otp_auth_service import (
    MAX_VERIFY_ATTEMPTS,
    PER_IP_VERIFY_HOURLY_CAP,
    RESEND_COOLDOWN_S,
    OtpAuthService,
)

pytestmark = pytest.mark.asyncio


class FakeRedis:
    """Minimal in-memory async Redis covering exactly what OtpAuthService uses.

    `ttl()` returns the `ex`/`expire` value last set — enough to drive the
    cooldown/cap retry-after reporting without a real clock.
    """

    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def get(self, key):
        v = self.store.get(key)
        return None if v is None else str(v).encode()

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
            self.ttls.pop(k, None)

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def incr(self, key):
        v = int(self.store.get(key, 0)) + 1
        self.store[key] = v
        return v

    async def expire(self, key, seconds):
        self.ttls[key] = seconds

    async def ttl(self, key):
        if key not in self.store:
            return -2
        return self.ttls.get(key, -1)


class GoTrueStub:
    def __init__(self) -> None:
        self.ensured: list[str] = []
        self.sends: list[str] = []
        self.verify_calls: list[tuple[str, str]] = []
        self.verify_result: dict | None = None

    async def ensure_consumer_user(self, email):
        self.ensured.append(email)

    async def send_email_otp(self, email):
        self.sends.append(email)

    async def verify_email_otp(self, email, code):
        self.verify_calls.append((email, code))
        return self.verify_result


@pytest.fixture
def gotrue_stub(monkeypatch):
    stub = GoTrueStub()
    gt = otp_mod.gotrue
    monkeypatch.setattr(gt, "ensure_consumer_user", stub.ensure_consumer_user)
    monkeypatch.setattr(gt, "send_email_otp", stub.send_email_otp)
    monkeypatch.setattr(gt, "verify_email_otp", stub.verify_email_otp)
    return stub


def _session(user_id: uuid.UUID) -> dict:
    return {
        "access_token": "acc-token",
        "token_type": "bearer",
        "expires_in": 3600,
        "refresh_token": "ref-token",
        "user": {"id": str(user_id)},
    }


# ── request_otp ────────────────────────────────────────────────────────────────


async def test_request_otp_sends_and_reports_cooldown(db, gotrue_stub):
    svc = OtpAuthService(db, FakeRedis())
    resp = await svc.request_otp("Me@Example.com ", "1.2.3.4")
    assert resp.retry_after_seconds == RESEND_COOLDOWN_S
    # Email is normalised (stripped + lowercased) before GoTrue is called.
    assert gotrue_stub.ensured == ["me@example.com"]
    assert gotrue_stub.sends == ["me@example.com"]


async def test_resend_within_cooldown_does_not_resend(db, gotrue_stub):
    svc = OtpAuthService(db, FakeRedis())
    await svc.request_otp("a@b.com", "1.2.3.4")
    resp = await svc.request_otp("a@b.com", "1.2.3.4")
    assert resp.retry_after_seconds == RESEND_COOLDOWN_S
    assert len(gotrue_stub.sends) == 1  # second request suppressed


async def test_per_email_send_cap(db, gotrue_stub):
    fake = FakeRedis()
    svc = OtpAuthService(db, fake)
    # Five sends allowed; clear the cooldown between them to simulate elapsed time
    # while staying inside the hourly window.
    for _ in range(5):
        await svc.request_otp("a@b.com", "1.2.3.4")
        await fake.delete("otp:cooldown:a@b.com")
    capped = await svc.request_otp("a@b.com", "1.2.3.4")
    assert capped.retry_after_seconds > 0
    assert len(gotrue_stub.sends) == 5  # sixth suppressed by the per-email cap


async def test_per_ip_send_cap(db, gotrue_stub):
    fake = FakeRedis()
    svc = OtpAuthService(db, fake)
    # Distinct emails (so neither cooldown nor the per-email cap interferes),
    # same IP → the 31st trips the per-IP cap of 30.
    for i in range(30):
        await svc.request_otp(f"user{i}@b.com", "9.9.9.9")
    capped = await svc.request_otp("user30@b.com", "9.9.9.9")
    assert capped.retry_after_seconds > 0
    assert len(gotrue_stub.sends) == 30


async def test_request_otp_without_redis_fails_closed(db, gotrue_stub):
    # #6 — with Redis down the send caps/cooldown can't be enforced, so we refuse
    # (503) rather than allow an uncapped flood; no code is ever sent.
    svc = OtpAuthService(db, None)
    with pytest.raises(HTTPException) as exc:
        await svc.request_otp("a@b.com", "1.2.3.4")
    assert exc.value.status_code == 503
    assert gotrue_stub.sends == []


# ── verify_otp ─────────────────────────────────────────────────────────────────


async def test_verify_success_bootstraps_new_user(db, seed, gotrue_stub):
    svc = OtpAuthService(db, FakeRedis())
    uid = uuid.uuid4()
    seed.user_ids.append(uid)  # cleanup the bootstrapped profile row
    gotrue_stub.verify_result = _session(uid)

    resp = await svc.verify_otp("New@Example.com ", "123456", "1.2.3.4")

    assert resp.is_new_user is True
    assert resp.access_token == "acc-token"
    assert resp.refresh_token == "ref-token"
    assert resp.expires_in == 3600
    profile = await UserProfileRepository(db).get_by_user_id(uid)
    assert profile is not None  # bootstrapped exactly once


async def test_verify_success_returning_user_not_new(db, seed, gotrue_stub):
    uid = uuid.uuid4()
    seed.user_ids.append(uid)
    await UserProfileRepository(db).get_or_create(uid, email=None)
    await db.commit()

    svc = OtpAuthService(db, FakeRedis())
    gotrue_stub.verify_result = _session(uid)
    resp = await svc.verify_otp("a@b.com", "123456", "1.2.3.4")
    assert resp.is_new_user is False


async def test_verify_wrong_code_reports_attempts_remaining(db, gotrue_stub):
    fake = FakeRedis()
    await fake.set("otp:issued:a@b.com", "1", ex=600)  # a live code exists
    svc = OtpAuthService(db, fake)
    gotrue_stub.verify_result = None  # GoTrue rejects

    with pytest.raises(HTTPException) as exc:
        await svc.verify_otp("a@b.com", "000000", "1.2.3.4")
    assert exc.value.status_code == 401
    assert exc.value.detail == {
        "code": "invalid_code",
        "attempts_remaining": MAX_VERIFY_ATTEMPTS - 1,
    }
    assert int(fake.store["otp:attempts:a@b.com"]) == 1


async def test_verify_expired_code(db, gotrue_stub):
    svc = OtpAuthService(db, FakeRedis())  # no issued marker → expired
    gotrue_stub.verify_result = None
    with pytest.raises(HTTPException) as exc:
        await svc.verify_otp("a@b.com", "000000", "1.2.3.4")
    assert exc.value.status_code == 401
    assert exc.value.detail == {"code": "code_expired"}


async def test_verify_locks_out_after_max_attempts(db, gotrue_stub):
    fake = FakeRedis()
    fake.store["otp:attempts:a@b.com"] = MAX_VERIFY_ATTEMPTS
    svc = OtpAuthService(db, fake)
    gotrue_stub.verify_result = _session(uuid.uuid4())  # must not be consulted

    with pytest.raises(HTTPException) as exc:
        await svc.verify_otp("a@b.com", "123456", "1.2.3.4")
    assert exc.value.status_code == 429
    assert exc.value.detail == {"code": "too_many_attempts"}
    assert gotrue_stub.verify_calls == []  # locked out before asking GoTrue


async def test_fresh_code_resets_attempt_budget(db, gotrue_stub):
    fake = FakeRedis()
    await fake.set("otp:issued:a@b.com", "1", ex=600)
    svc = OtpAuthService(db, fake)
    gotrue_stub.verify_result = None

    with pytest.raises(HTTPException):
        await svc.verify_otp("a@b.com", "000000", "1.2.3.4")
    assert int(fake.store["otp:attempts:a@b.com"]) == 1

    # Requesting a fresh code clears the old code's attempt budget.
    await svc.request_otp("a@b.com", "1.2.3.4")
    assert "otp:attempts:a@b.com" not in fake.store


async def test_verify_per_ip_cap_across_emails(db, gotrue_stub):
    # #6 — one IP spraying verify against many DISTINCT emails (so the per-email
    # 5-attempt lockout never trips) is still bounded by the per-IP hourly cap.
    fake = FakeRedis()
    svc = OtpAuthService(db, fake)
    # every guess rejected (no issued marker → treated as expired)
    gotrue_stub.verify_result = None

    for i in range(PER_IP_VERIFY_HOURLY_CAP):
        with pytest.raises(HTTPException) as exc:
            await svc.verify_otp(f"user{i}@b.com", "000000", "9.9.9.9")
        assert exc.value.status_code == 401  # code_expired, not yet capped
    # The next attempt from the same IP trips the per-IP cap.
    with pytest.raises(HTTPException) as exc:
        await svc.verify_otp("another@b.com", "000000", "9.9.9.9")
    assert exc.value.status_code == 429
    assert exc.value.detail == {"code": "too_many_attempts"}


async def test_verify_without_redis_fails_closed(db, gotrue_stub):
    # #6 — verify lockout lives in Redis; with it down we fail closed (503)
    # rather than allow unbounded guessing against a target email.
    svc = OtpAuthService(db, None)
    gotrue_stub.verify_result = None
    with pytest.raises(HTTPException) as exc:
        await svc.verify_otp("a@b.com", "000000", "1.2.3.4")
    assert exc.value.status_code == 503
    assert gotrue_stub.verify_calls == []  # never consulted GoTrue
