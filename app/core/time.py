"""Shared time constants.

Asia/Dhaka is a fixed UTC+6 offset with no DST, so we model it directly rather
than depending on the IANA tz database. This is the single source of truth —
the digest scheduler, the community feed, and the StreakEngine all import it so
their day boundaries can never drift apart.
"""

from datetime import timedelta, timezone

DHAKA_TZ = timezone(timedelta(hours=6))
