"""Goal endpoint contract tests (PRD 08 §Goals, committed target).

Drives the public HTTP surface (in-process httpx, get_db override — the
test_journal_contract.py pattern): template instantiation, free-form validation,
journal-fed Khatm progress + pace, recurring check-off idempotency / un-check,
and ownership scoping. Dates anchor to the real Asia/Dhaka day so journal
backfill windows behave (today is always editable).
"""

from datetime import datetime, timedelta, timezone

from app.services.streak_engine import DHAKA_TZ
from tests.conftest import auth_headers

TODAY = datetime.now(timezone.utc).astimezone(DHAKA_TZ).date()


# ── Templates ─────────────────────────────────────────────────────────────────


async def test_khatm_template_requires_dates(client, seed):
    user = await seed.user()
    await seed.commit()
    resp = await client.post(
        "/users/me/goals/templates",
        headers=auth_headers(user),
        json={"template": "khatm_ramadan"},  # no window
    )
    assert resp.status_code == 422, resp.text


async def test_khatm_template_creates_quran_goal(client, seed):
    user = await seed.user()
    await seed.commit()
    end = TODAY + timedelta(days=29)
    resp = await client.post(
        "/users/me/goals/templates",
        headers=auth_headers(user),
        json={
            "template": "khatm_ramadan",
            "start_date": TODAY.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["goal_kind"] == "quran_quantity"
    assert body["template"] == "khatm_ramadan"
    assert body["target_amount"] == 604
    assert body["unit"] == "pages"
    assert body["progress"]["current_amount"] == 0
    assert body["progress"]["days_remaining"] == 30
    assert body["progress"]["daily_pace"] == 21  # ceil(604/30)


async def test_recitation_templates_are_one_tap(client, seed):
    user = await seed.user()
    await seed.commit()
    h = auth_headers(user)
    for key, recurrence in [("ayat_al_kursi", "daily"), ("surah_al_kahf", "weekly")]:
        resp = await client.post(
            "/users/me/goals/templates", headers=h, json={"template": key}
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["goal_kind"] == "recurring"
        assert body["recurrence"] == recurrence
        assert body["progress"]["total_completions"] == 0


# ── Free-form validation ──────────────────────────────────────────────────────


async def test_freeform_quantity_requires_full_window(client, seed):
    user = await seed.user()
    await seed.commit()
    resp = await client.post(
        "/users/me/goals",
        headers=auth_headers(user),
        json={"goal_kind": "quran_quantity", "title": "Read more", "target_amount": 30},
    )
    assert resp.status_code == 422, resp.text


async def test_freeform_recurring_rejects_quantity_fields(client, seed):
    user = await seed.user()
    await seed.commit()
    resp = await client.post(
        "/users/me/goals",
        headers=auth_headers(user),
        json={
            "goal_kind": "recurring",
            "title": "Dhikr",
            "recurrence": "daily",
            "target_amount": 5,  # not valid for a recurring goal
        },
    )
    assert resp.status_code == 422, resp.text


async def test_freeform_recurring_created(client, seed):
    user = await seed.user()
    await seed.commit()
    resp = await client.post(
        "/users/me/goals",
        headers=auth_headers(user),
        json={
            "goal_kind": "recurring",
            "title": "Morning adhkar",
            "recurrence": "daily",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["template"] is None


# ── Journal-fed progress ──────────────────────────────────────────────────────


async def test_quran_progress_is_journal_fed(client, seed):
    user = await seed.user()
    await seed.commit()
    h = auth_headers(user)
    end = TODAY + timedelta(days=9)
    goal = (
        await client.post(
            "/users/me/goals",
            headers=h,
            json={
                "goal_kind": "quran_quantity",
                "title": "Custom Khatm",
                "target_amount": 100,
                "unit": "pages",
                "start_date": TODAY.isoformat(),
                "end_date": end.isoformat(),
            },
        )
    ).json()
    goal_id = goal["goal_id"]
    assert goal["progress"]["daily_pace"] == 10  # ceil(100/10)

    # Log 20 pages today — the goal's progress should pick it up automatically.
    await client.post(
        "/users/me/journal",
        headers=h,
        json={
            "entry_date": TODAY.isoformat(),
            "quran": {"amount": 20, "unit": "pages"},
        },
    )
    refreshed = (await client.get(f"/users/me/goals/{goal_id}", headers=h)).json()
    assert refreshed["progress"]["current_amount"] == 20
    assert refreshed["progress"]["remaining"] == 80
    assert refreshed["progress"]["daily_pace"] == 8  # ceil(80/10)


async def test_quran_progress_ignores_mismatched_unit(client, seed):
    user = await seed.user()
    await seed.commit()
    h = auth_headers(user)
    end = TODAY + timedelta(days=9)
    goal = (
        await client.post(
            "/users/me/goals",
            headers=h,
            json={
                "goal_kind": "quran_quantity",
                "title": "Pages goal",
                "target_amount": 100,
                "unit": "pages",
                "start_date": TODAY.isoformat(),
                "end_date": end.isoformat(),
            },
        )
    ).json()
    # A juz log must not feed a pages goal — conversion is a client concern.
    await client.post(
        "/users/me/journal",
        headers=h,
        json={"entry_date": TODAY.isoformat(), "quran": {"amount": 2, "unit": "juz"}},
    )
    refreshed = (
        await client.get(f"/users/me/goals/{goal['goal_id']}", headers=h)
    ).json()
    assert refreshed["progress"]["current_amount"] == 0


async def test_list_goals_batched_progress_matches(client, seed):
    """The list endpoint computes progress via the batched (no-N+1) path — assert
    it yields the same per-goal numbers the single-goal build would: unit-scoped
    Qur'an sums over each goal's window, and per-goal recurring completions."""
    user = await seed.user()
    await seed.commit()
    h = auth_headers(user)
    end = TODAY + timedelta(days=9)

    pages_goal = (
        await client.post(
            "/users/me/goals",
            headers=h,
            json={
                "goal_kind": "quran_quantity",
                "title": "Pages goal",
                "target_amount": 100,
                "unit": "pages",
                "start_date": TODAY.isoformat(),
                "end_date": end.isoformat(),
            },
        )
    ).json()
    juz_goal = (
        await client.post(
            "/users/me/goals",
            headers=h,
            json={
                "goal_kind": "quran_quantity",
                "title": "Juz goal",
                "target_amount": 30,
                "unit": "juz",
                "start_date": TODAY.isoformat(),
                "end_date": end.isoformat(),
            },
        )
    ).json()
    recurring_goal = (
        await client.post(
            "/users/me/goals/templates", headers=h, json={"template": "ayat_al_kursi"}
        )
    ).json()

    # Log 20 pages today and check off the recurring goal once.
    await client.post(
        "/users/me/journal",
        headers=h,
        json={
            "entry_date": TODAY.isoformat(),
            "quran": {"amount": 20, "unit": "pages"},
        },
    )
    await client.post(
        f"/users/me/goals/{recurring_goal['goal_id']}/completions", headers=h, json={}
    )

    items = (await client.get("/users/me/goals", headers=h)).json()["items"]
    by_id = {g["goal_id"]: g for g in items}

    # Pages goal picks up the matching-unit log; juz goal must not (unit-scoped).
    assert by_id[pages_goal["goal_id"]]["progress"]["current_amount"] == 20
    assert by_id[juz_goal["goal_id"]]["progress"]["current_amount"] == 0
    # Recurring goal reflects its own single completion, not the other goals'.
    assert by_id[recurring_goal["goal_id"]]["progress"]["total_completions"] == 1
    assert by_id[recurring_goal["goal_id"]]["progress"]["done_this_period"] is True


# ── Recurring check-off ───────────────────────────────────────────────────────


async def test_checkoff_is_idempotent_and_uncheckable(client, seed):
    user = await seed.user()
    await seed.commit()
    h = auth_headers(user)
    goal = (
        await client.post(
            "/users/me/goals/templates", headers=h, json={"template": "ayat_al_kursi"}
        )
    ).json()
    goal_id = goal["goal_id"]

    # Two taps on the same (default = today) date → one completion.
    await client.post(f"/users/me/goals/{goal_id}/completions", headers=h, json={})
    second = await client.post(
        f"/users/me/goals/{goal_id}/completions", headers=h, json={}
    )
    assert second.status_code == 200, second.text
    assert second.json()["progress"]["total_completions"] == 1
    assert second.json()["progress"]["done_this_period"] is True

    # Un-check today.
    un = await client.delete(
        f"/users/me/goals/{goal_id}/completions/{TODAY.isoformat()}", headers=h
    )
    assert un.status_code == 200, un.text
    assert un.json()["progress"]["total_completions"] == 0
    assert un.json()["progress"]["done_this_period"] is False


async def test_checkoff_rejected_on_quran_goal(client, seed):
    user = await seed.user()
    await seed.commit()
    h = auth_headers(user)
    end = TODAY + timedelta(days=29)
    goal = (
        await client.post(
            "/users/me/goals/templates",
            headers=h,
            json={
                "template": "khatm_ramadan",
                "start_date": TODAY.isoformat(),
                "end_date": end.isoformat(),
            },
        )
    ).json()
    resp = await client.post(
        f"/users/me/goals/{goal['goal_id']}/completions", headers=h, json={}
    )
    assert resp.status_code == 400, resp.text


# ── Lifecycle & ownership ─────────────────────────────────────────────────────


async def test_pause_and_list_filter(client, seed):
    user = await seed.user()
    await seed.commit()
    h = auth_headers(user)
    goal = (
        await client.post(
            "/users/me/goals",
            headers=h,
            json={"goal_kind": "recurring", "title": "Tahajjud", "recurrence": "daily"},
        )
    ).json()
    paused = await client.patch(
        f"/users/me/goals/{goal['goal_id']}", headers=h, json={"status": "paused"}
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"

    active_only = (await client.get("/users/me/goals?status=active", headers=h)).json()
    assert all(g["status"] == "active" for g in active_only["items"])
    paused_only = (await client.get("/users/me/goals?status=paused", headers=h)).json()
    assert paused_only["total"] == 1


async def test_goal_is_owner_scoped(client, seed):
    owner = await seed.user()
    other = await seed.user()
    await seed.commit()
    goal = (
        await client.post(
            "/users/me/goals",
            headers=auth_headers(owner),
            json={"goal_kind": "recurring", "title": "Private", "recurrence": "daily"},
        )
    ).json()
    # A different user can't see it.
    resp = await client.get(
        f"/users/me/goals/{goal['goal_id']}", headers=auth_headers(other)
    )
    assert resp.status_code == 404, resp.text


async def test_delete_goal(client, seed):
    user = await seed.user()
    await seed.commit()
    h = auth_headers(user)
    goal = (
        await client.post(
            "/users/me/goals",
            headers=h,
            json={"goal_kind": "recurring", "title": "Temp", "recurrence": "weekly"},
        )
    ).json()
    deleted = await client.delete(f"/users/me/goals/{goal['goal_id']}", headers=h)
    assert deleted.status_code == 204, deleted.text
    gone = await client.get(f"/users/me/goals/{goal['goal_id']}", headers=h)
    assert gone.status_code == 404


async def test_delete_goal_with_completions_cascades(client, seed):
    """Deleting a goal that has check-offs must cascade them, not 500 — the
    completions relationship is lazy='raise', so the delete path must not try to
    load the collection (passive_deletes + DB ON DELETE CASCADE)."""
    user = await seed.user()
    await seed.commit()
    h = auth_headers(user)
    goal = (
        await client.post(
            "/users/me/goals",
            headers=h,
            json={"goal_kind": "recurring", "title": "Habit", "recurrence": "daily"},
        )
    ).json()
    gid = goal["goal_id"]
    # Create a completion row so the cascade actually has children to remove.
    await client.post(f"/users/me/goals/{gid}/completions", headers=h, json={})

    deleted = await client.delete(f"/users/me/goals/{gid}", headers=h)
    assert deleted.status_code == 204, deleted.text
    assert (await client.get(f"/users/me/goals/{gid}", headers=h)).status_code == 404


async def test_future_checkoff_rejected(client, seed):
    """A completion can't be logged for a future date — it would fabricate a
    streak that never happened."""
    user = await seed.user()
    await seed.commit()
    h = auth_headers(user)
    goal = (
        await client.post(
            "/users/me/goals/templates", headers=h, json={"template": "ayat_al_kursi"}
        )
    ).json()
    future = (TODAY + timedelta(days=5)).isoformat()
    resp = await client.post(
        f"/users/me/goals/{goal['goal_id']}/completions",
        headers=h,
        json={"completion_date": future},
    )
    assert resp.status_code == 400, resp.text
