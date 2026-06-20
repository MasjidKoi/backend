"""PRD 03 push event layer — TIME_CHANGE, HIJRI_OFFSET, PLATFORM_PUSH.

The suite pins PUSH_ENABLED=false (conftest), so the real default transport is
the no-op LoggingTransport. These tests instead monkeypatch the transport
factory with a CaptureTransport that records every fan-out, letting us assert
the exact audience + message type without a network call.
"""

from app.models.device_token import DeviceToken
from app.models.enums import PushMessageType
from app.services import push_service as push_module
from app.services.push_service import SendResult
from tests.conftest import auth_headers


class CaptureTransport:
    """Records (tokens, message) for every send; satisfies PushTransport."""

    def __init__(self) -> None:
        self.sends: list[tuple[list[str], object]] = []

    async def send(self, tokens, message) -> SendResult:
        self.sends.append((list(tokens), message))
        return SendResult(accepted=len(tokens))


def _install_capture(monkeypatch) -> CaptureTransport:
    cap = CaptureTransport()
    monkeypatch.setattr(push_module, "_build_default_transport", lambda: cap)
    return cap


def _of_type(cap: CaptureTransport, mtype: PushMessageType) -> list:
    return [msg for _, msg in cap.sends if msg.message_type == mtype]


# ── TIME_CHANGE ───────────────────────────────────────────────────────────────


async def test_jumah_update_notifies_non_muted_followers(client, seed, db, monkeypatch):
    m = await seed.masjid()
    admin = await seed.user()
    inst = await seed.user()
    dig = await seed.user()
    mut = await seed.user()
    await seed.follow(inst, m.masjid_id, mode="instant")
    await seed.follow(dig, m.masjid_id, mode="digest")
    await seed.follow(mut, m.masjid_id, mode="mute")
    db.add(DeviceToken(token="tok-inst", user_id=inst, platform="android"))
    db.add(DeviceToken(token="tok-dig", user_id=dig, platform="android"))
    db.add(DeviceToken(token="tok-mut", user_id=mut, platform="android"))
    await seed.commit()

    cap = _install_capture(monkeypatch)
    hdrs = auth_headers(admin, role="masjid_admin", masjid_id=m.masjid_id)
    r = await client.put(
        f"/masjids/{m.masjid_id}/jumah",
        json={"notes": "Khutbah moved to 1:30pm"},
        headers=hdrs,
    )
    assert r.status_code == 200, r.text

    pings = _of_type(cap, PushMessageType.TIME_CHANGE)
    assert len(pings) == 1
    # instant + digest receive it; mute is excluded.
    assert set(cap.sends[0][0]) == {"tok-inst", "tok-dig"}
    assert pings[0].data["masjid_id"] == str(m.masjid_id)
    # Jumu'ah is a standing schedule — no specific date in the payload.
    assert "date" not in pings[0].data


async def test_manual_override_time_change_carries_date(client, seed, db, monkeypatch):
    m = await seed.masjid()
    admin = await seed.user()
    follower = await seed.user()
    await seed.follow(follower, m.masjid_id, mode="instant")
    db.add(DeviceToken(token="tok-mo", user_id=follower, platform="android"))
    await seed.commit()

    cap = _install_capture(monkeypatch)
    hdrs = auth_headers(admin, role="masjid_admin", masjid_id=m.masjid_id)
    r = await client.put(
        f"/masjids/{m.masjid_id}/prayer-times",
        json={"date": "2026-06-21", "fajr_iqamah": "04:45"},
        headers=hdrs,
    )
    assert r.status_code == 200, r.text

    pings = _of_type(cap, PushMessageType.TIME_CHANGE)
    assert len(pings) == 1
    assert pings[0].data["date"] == "2026-06-21"
    assert set(cap.sends[-1][0]) == {"tok-mo"}


async def test_recalc_fires_time_change(client, seed, db, monkeypatch):
    m = await seed.masjid()
    admin = await seed.user()
    follower = await seed.user()
    await seed.follow(follower, m.masjid_id, mode="digest")
    db.add(DeviceToken(token="tok-rc", user_id=follower, platform="ios"))
    await seed.commit()

    cap = _install_capture(monkeypatch)
    hdrs = auth_headers(admin, role="masjid_admin", masjid_id=m.masjid_id)
    r = await client.post(
        f"/masjids/{m.masjid_id}/prayer-times/recalc",
        json={"date": "2026-06-21"},
        headers=hdrs,
    )
    assert r.status_code == 200, r.text

    pings = _of_type(cap, PushMessageType.TIME_CHANGE)
    assert len(pings) == 1
    assert pings[0].data["date"] == "2026-06-21"
    assert set(cap.sends[-1][0]) == {"tok-rc"}


async def test_time_change_skipped_when_no_followers(client, seed, monkeypatch):
    m = await seed.masjid()
    admin = await seed.user()
    await seed.commit()

    cap = _install_capture(monkeypatch)
    hdrs = auth_headers(admin, role="masjid_admin", masjid_id=m.masjid_id)
    r = await client.put(
        f"/masjids/{m.masjid_id}/jumah", json={"notes": "n/a"}, headers=hdrs
    )
    assert r.status_code == 200
    # No followers → no devices resolved → nothing dispatched.
    assert _of_type(cap, PushMessageType.TIME_CHANGE) == []


# ── Hijri offset + public app-config ────────────────────────────────────────────


async def test_app_config_is_public_and_exposes_offset(client):
    r = await client.get("/app-config")
    assert r.status_code == 200
    body = r.json()
    assert "hijri_offset_days" in body
    assert "default_calc_method" in body
    assert "default_madhab" in body
    # Admin-only / sensitive fields must not leak into the public config.
    assert "terms_of_service" not in body
    assert "updated_by_email" not in body


async def test_settings_rejects_out_of_range_offset(client, seed):
    admin = await seed.user()
    hdrs = auth_headers(admin, role="platform_admin")
    assert (
        await client.patch(
            "/admin/settings", json={"hijri_offset_days": 3}, headers=hdrs
        )
    ).status_code == 422
    assert (
        await client.patch(
            "/admin/settings", json={"hijri_offset_days": -3}, headers=hdrs
        )
    ).status_code == 422


async def test_hijri_offset_change_broadcasts_once(client, seed, db, monkeypatch):
    admin = await seed.user()
    follower = await seed.user()
    db.add(DeviceToken(token="tok-hijri", user_id=follower, platform="android"))
    await seed.commit()
    hdrs = auth_headers(admin, role="platform_admin")

    # platform_settings is a shared singleton — restore the original value.
    orig = (await client.get("/app-config")).json()["hijri_offset_days"]
    try:
        # Baseline to 0 before installing the capture (any ping here is ignored).
        await client.patch(
            "/admin/settings", json={"hijri_offset_days": 0}, headers=hdrs
        )
        cap = _install_capture(monkeypatch)

        # 0 → 1 fires exactly one platform-wide HIJRI_OFFSET broadcast.
        r = await client.patch(
            "/admin/settings", json={"hijri_offset_days": 1}, headers=hdrs
        )
        assert r.status_code == 200
        assert r.json()["hijri_offset_days"] == 1
        pings = _of_type(cap, PushMessageType.HIJRI_OFFSET)
        assert len(pings) == 1
        assert pings[0].data["hijri_offset_days"] == 1
        assert "tok-hijri" in cap.sends[0][0]  # broadcast reaches every device

        # A PATCH that doesn't change the value fires nothing.
        cap.sends.clear()
        r = await client.patch(
            "/admin/settings", json={"hijri_offset_days": 1}, headers=hdrs
        )
        assert r.status_code == 200
        assert _of_type(cap, PushMessageType.HIJRI_OFFSET) == []
    finally:
        await client.patch(
            "/admin/settings", json={"hijri_offset_days": orig}, headers=hdrs
        )


# ── PLATFORM_PUSH broadcast endpoint ────────────────────────────────────────────


async def test_broadcast_requires_platform_admin(client, seed):
    user = await seed.user()
    await seed.commit()
    r = await client.post(
        "/admin/broadcast-push",
        json={"title": "Eid", "body": "Eid Mubarak"},
        headers=auth_headers(user),  # plain app_user
    )
    assert r.status_code == 403


async def test_broadcast_sends_platform_push_to_all_devices(
    client, seed, db, monkeypatch
):
    admin = await seed.user()
    u1 = await seed.user()
    u2 = await seed.user()
    db.add(DeviceToken(token="tok-b1", user_id=u1, platform="android"))
    db.add(DeviceToken(token="tok-b2", user_id=u2, platform="ios"))
    await seed.commit()

    cap = _install_capture(monkeypatch)
    hdrs = auth_headers(admin, role="platform_admin")
    r = await client.post(
        "/admin/broadcast-push",
        json={
            "title": "Eid Mubarak",
            "body": "Eid is tomorrow, in sha Allah.",
            "data": {"deep_link": "home"},
        },
        headers=hdrs,
    )
    assert r.status_code == 200
    assert r.json()["devices_notified"] >= 2

    assert len(cap.sends) == 1
    tokens, msg = cap.sends[0]
    assert msg.message_type == PushMessageType.PLATFORM_PUSH
    assert msg.title == "Eid Mubarak"
    assert msg.data == {"deep_link": "home"}
    assert {"tok-b1", "tok-b2"}.issubset(set(tokens))
