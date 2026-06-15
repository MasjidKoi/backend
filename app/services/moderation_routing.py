"""Shared moderation-routing predicate (Gap #10, PRD 04).

A single **pure** decision over ``(masjid-has-claimed-admin, pending-since, now)``
that says which moderation queue a pending community item belongs to. Both
content types that have a moderation lifecycle — community photos (#8) and masjid
questions (#9) — call this *one* implementation so the 7-day rule can never drift
between them.

Routing:
  * masjid HAS a claimed admin  → the masjid admin's queue
  * masjid has NO claimed admin  → the NGO (platform) central queue
  * anything pending **> 7 days** → ALSO visible to the NGO — a shared-visibility
    safety net, NOT a handoff: the masjid admin keeps it too.

Authorisation falls out of the same routing:
  * a masjid admin may always moderate their own masjid's items (they *are* the
    claimed admin — the 7-day clock never takes work away from them);
  * a platform admin (NGO) may moderate an item only when it is routed to the NGO
    queue (unclaimed masjid, or pending past the SLA).

The module is pure — no I/O, no clock reads. Callers pass ``now`` and the
DB-derived ``masjid_has_claimed_admin`` in, which keeps it trivially testable.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

# An item left pending this long becomes visible to the NGO central queue even
# when the masjid has its own claimed admin (shared visibility, not a handoff).
MODERATION_SLA = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class QueueRouting:
    """Which moderation queues a single pending item appears in."""

    masjid_admin: bool  # the masjid's own claimed admin
    ngo: bool  # the NGO / platform central queue


def route_pending_item(
    *,
    masjid_has_claimed_admin: bool,
    pending_since: datetime,
    now: datetime,
) -> QueueRouting:
    """Pure: decide a pending item's queue visibility. No I/O, no side effects."""
    overdue = now - pending_since >= MODERATION_SLA
    return QueueRouting(
        masjid_admin=masjid_has_claimed_admin,
        ngo=(not masjid_has_claimed_admin) or overdue,
    )


def ngo_pending_cutoff(now: datetime) -> datetime:
    """Pending items created at/before this instant are overdue → NGO-visible.

    The SQL embodiment of the 7-day arm of :func:`route_pending_item`, used by the
    list endpoints so totals/pagination stay correct instead of post-filtering.
    """
    return now - MODERATION_SLA


def can_moderate(
    *,
    is_platform_admin: bool,
    user_masjid_id: uuid.UUID | None,
    item_masjid_id: uuid.UUID,
    masjid_has_claimed_admin: bool,
    pending_since: datetime,
    now: datetime,
) -> bool:
    """Pure: may this user act on this *pending* item, per the routing above?"""
    if is_platform_admin:
        return route_pending_item(
            masjid_has_claimed_admin=masjid_has_claimed_admin,
            pending_since=pending_since,
            now=now,
        ).ngo
    # Masjid admin: owning the masjid scope is the claim. The SLA never restricts
    # the masjid admin — it only *adds* NGO visibility on top.
    return user_masjid_id is not None and user_masjid_id == item_masjid_id
