"""Daily-digest scheduler (PRD 07, gap #16).

An hourly bucketing job: each run serves the bucket of users whose chosen
``digest_hour`` matches the current Asia/Dhaka hour. For each due user it
collects announcements published since their last digest (bounded 24 h) from
follows in *digest* mode, and — only if there's something to say — sends one
push summarising count + masjid count, deep-linking to the Feed tab.

Invariants the committed tests pin down:
- hour-bucket selection against Asia/Dhaka,
- digest/instant/mute routing (instant items never digested; muted never sent),
- empty-digest suppression (no push, no stamp),
- one-push-per-user-per-day via ``last_digest_sent_at`` (Dhaka calendar day),
- idempotency across a re-run within the same hour.

The collection/routing logic lives in ``run_for_hour(hour, now)`` so it is
exercisable with controlled inputs; ``run_due()`` is the thin scheduler wrapper
that computes the current Dhaka hour.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import DHAKA_TZ
from app.models.enums import PushMessageType
from app.repositories.announcement_repository import AnnouncementRepository
from app.repositories.user_masjid_follow_repository import UserMasjidFollowRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.services.push_service import PushMessage, PushService

logger = logging.getLogger(__name__)

DIGEST_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class DigestSend:
    """What one user's digest contained — the testable output of a run."""

    user_id: str
    announcement_count: int
    masjid_count: int


class DigestService:
    def __init__(self, db: AsyncSession, push: PushService | None = None) -> None:
        self.db = db
        self.profile_repo = UserProfileRepository(db)
        self.follow_repo = UserMasjidFollowRepository(db)
        self.ann_repo = AnnouncementRepository(db)
        self.push = push or PushService(db)

    async def run_due(self) -> list[DigestSend]:
        now = datetime.now(timezone.utc)
        dhaka_hour = now.astimezone(DHAKA_TZ).hour
        return await self.run_for_hour(dhaka_hour, now)

    async def run_for_hour(self, hour: int, now: datetime) -> list[DigestSend]:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        today_dhaka = now.astimezone(DHAKA_TZ).date()

        candidates = await self.profile_repo.list_by_digest_hour(hour)
        sends: list[DigestSend] = []

        for profile in candidates:
            # One push per user per Dhaka day — skip anyone already served today.
            if (
                profile.last_digest_sent_at is not None
                and profile.last_digest_sent_at.astimezone(DHAKA_TZ).date()
                >= today_dhaka
            ):
                continue

            masjid_ids = await self.follow_repo.list_digest_masjid_ids_for_user(
                profile.user_id
            )
            if not masjid_ids:
                continue

            # Collect since the last digest, bounded to a 24 h look-back so a
            # long-dormant user isn't hit with a week of backlog.
            floor = now - DIGEST_WINDOW
            since = (
                max(profile.last_digest_sent_at, floor)
                if profile.last_digest_sent_at is not None
                else floor
            )
            (
                ann_count,
                masjid_count,
            ) = await self.ann_repo.count_published_since_for_masjids(
                masjid_ids, since, now
            )
            if ann_count == 0:
                # Empty-digest suppression: no push, and no stamp so the user
                # stays eligible should something publish before the next run.
                continue

            message = PushMessage(
                message_type=PushMessageType.DAILY_DIGEST,
                title="New from your masjids",
                body=(
                    f"{ann_count} new announcement"
                    f"{'s' if ann_count != 1 else ''} from "
                    f"{masjid_count} masjid{'s' if masjid_count != 1 else ''}"
                ),
                data={"deep_link": "feed"},
            )
            await self.push.notify_users([profile.user_id], message)

            await self.profile_repo.update(profile, {"last_digest_sent_at": now})
            await self.profile_repo.commit()
            sends.append(
                DigestSend(
                    user_id=str(profile.user_id),
                    announcement_count=ann_count,
                    masjid_count=masjid_count,
                )
            )

        if sends:
            logger.info("Digest job (hour=%d): sent %d digest(s)", hour, len(sends))
        return sends
