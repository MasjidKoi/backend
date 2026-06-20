"""Review upsert tests (PRD 07, gap #13).

Through the HTTP interface: create-vs-replace semantics, the edited marker on
replacement, and the conditional low-star body rule.
"""

from tests.conftest import auth_headers


async def test_put_creates_then_replaces_with_edited_marker(client, seed):
    user = await seed.user()
    m = await seed.masjid()
    await seed.commit()
    hdrs = auth_headers(user)
    url = f"/masjids/{m.masjid_id}/reviews"

    # Create
    r1 = await client.put(url, json={"rating": 5, "body": "Lovely"}, headers=hdrs)
    assert r1.status_code == 200
    first = r1.json()
    assert first["rating"] == 5
    assert first["edited"] is False

    # Replace — same review id, edited stamped
    r2 = await client.put(url, json={"rating": 4, "body": "Still good"}, headers=hdrs)
    assert r2.status_code == 200
    second = r2.json()
    assert second["review_id"] == first["review_id"]
    assert second["rating"] == 4
    assert second["edited"] is True

    # Exactly one review exists for this masjid
    listing = (await client.get(url)).json()
    assert listing["total"] == 1


async def test_low_star_requires_body(client, seed):
    user = await seed.user()
    m = await seed.masjid()
    await seed.commit()
    hdrs = auth_headers(user)
    url = f"/masjids/{m.masjid_id}/reviews"

    # 1-star with no body → rejected
    r = await client.put(url, json={"rating": 1}, headers=hdrs)
    assert r.status_code == 422

    # 1-star with short body → rejected
    r = await client.put(url, json={"rating": 1, "body": "bad"}, headers=hdrs)
    assert r.status_code == 422

    # 1-star with sufficient body → accepted
    r = await client.put(
        url,
        json={"rating": 1, "body": "The wudu area was closed during my whole visit."},
        headers=hdrs,
    )
    assert r.status_code == 200

    # 5-star stars-only → accepted
    user2 = await seed.user()
    await seed.commit()
    r = await client.put(url, json={"rating": 5}, headers=auth_headers(user2))
    assert r.status_code == 200
