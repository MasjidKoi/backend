"""Feed endpoint contract tests (PRD 07, committed target).

Exercised through the HTTP interface: only followed masjids' items appear,
only published announcements, only future events, correct per-type ordering,
cursor stability across pages, and the embedded attendee/RSVP fields.
"""

from datetime import datetime, timedelta, timezone

from tests.conftest import auth_headers

ANN_FEED = "/users/me/feed?type=announcements"


async def test_feed_only_followed_and_published_announcements(client, seed):
    user = await seed.user()
    followed = await seed.masjid("Followed")
    other = await seed.masjid("Not followed")
    await seed.follow(user, followed.masjid_id)
    await seed.announcement(followed.masjid_id, title="Visible")
    await seed.announcement(followed.masjid_id, title="Draft", published=False)
    await seed.announcement(other.masjid_id, title="Unfollowed masjid")
    await seed.commit()

    r = await client.get(ANN_FEED, headers=auth_headers(user))
    assert r.status_code == 200
    titles = [i["title"] for i in r.json()["items"]]
    assert titles == ["Visible"]  # draft + unfollowed masjid excluded


async def test_feed_announcements_newest_first(client, seed):
    user = await seed.user()
    m = await seed.masjid()
    await seed.follow(user, m.masjid_id)
    now = datetime.now(timezone.utc)
    await seed.announcement(m.masjid_id, title="oldest", published_at=now - timedelta(hours=3))
    await seed.announcement(m.masjid_id, title="middle", published_at=now - timedelta(hours=2))
    await seed.announcement(m.masjid_id, title="newest", published_at=now - timedelta(hours=1))
    await seed.commit()

    r = await client.get(ANN_FEED, headers=auth_headers(user))
    titles = [i["title"] for i in r.json()["items"]]
    assert titles == ["newest", "middle", "oldest"]


async def test_feed_announcements_cursor_pagination_stable(client, seed):
    user = await seed.user()
    m = await seed.masjid()
    await seed.follow(user, m.masjid_id)
    now = datetime.now(timezone.utc)
    for i in range(5):
        await seed.announcement(
            m.masjid_id, title=f"a{i}", published_at=now - timedelta(hours=5 - i)
        )
    await seed.commit()

    seen = []
    cursor = None
    for _ in range(5):  # guard against infinite loop
        url = "/users/me/feed?type=announcements&limit=2"
        if cursor:
            url += f"&cursor={cursor}"
        body = (await client.get(url, headers=auth_headers(user))).json()
        seen.extend(i["title"] for i in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    # newest-first, no dupes, no gaps across page boundaries
    assert seen == ["a4", "a3", "a2", "a1", "a0"]
    assert len(set(seen)) == 5


async def test_feed_events_future_only_with_attendee_and_rsvp(client, seed):
    user = await seed.user()
    m = await seed.masjid()
    await seed.follow(user, m.masjid_id)
    today = datetime.now(timezone.utc).date()
    await seed.event(m.masjid_id, event_date=today - timedelta(days=2), title="past")
    future = await seed.event(
        m.masjid_id, event_date=today + timedelta(days=3), title="future"
    )
    # caller RSVPs the future event
    from app.models.masjid_event import EventRsvp

    seed.db.add(EventRsvp(event_id=future.event_id, user_id=user))
    await seed.commit()

    r = await client.get("/users/me/feed?type=events", headers=auth_headers(user))
    items = r.json()["items"]
    assert [i["title"] for i in items] == ["future"]  # past excluded server-side
    assert items[0]["attendee_count"] == 1
    assert items[0]["is_rsvped"] is True
    assert items[0]["masjid_name"] == m.name


async def test_feed_events_soonest_first(client, seed):
    user = await seed.user()
    m = await seed.masjid()
    await seed.follow(user, m.masjid_id)
    today = datetime.now(timezone.utc).date()
    await seed.event(m.masjid_id, event_date=today + timedelta(days=5), title="later")
    await seed.event(m.masjid_id, event_date=today + timedelta(days=1), title="sooner")
    await seed.commit()

    r = await client.get("/users/me/feed?type=events", headers=auth_headers(user))
    assert [i["title"] for i in r.json()["items"]] == ["sooner", "later"]


async def test_feed_requires_auth(client):
    r = await client.get("/users/me/feed?type=announcements")
    assert r.status_code in (401, 403)
