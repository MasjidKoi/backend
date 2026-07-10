"""Account-deletion purge (PRD 09 #1).

``DELETE /users/me`` only soft-deletes (flips ``is_deleted`` + stamps
``deletion_requested_at``); this service is the scheduled consumer that, once the
30-day window has elapsed, *anonymises* the account — every content row is kept
but re-keyed off the real identity (see AccountPurgeRepository) and the profile is
reduced to a stripped tombstone. It honours the 202 promise ("data purged within
30 days") without breaking the masjids' financial books.

Run from ``core/scheduler.py`` on a daily tick. Each account is anonymised in its
own transaction so one failure can't poison the rest of the sweep, and the
``purged_at`` stamp makes re-runs idempotent.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user_profile import UserProfile
from app.repositories.account_purge_repository import AccountPurgeRepository
from app.services.gotrue_client import gotrue

logger = logging.getLogger(__name__)


class AccountPurgeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = AccountPurgeRepository(db)

    async def run_due(self, *, window_days: int | None = None, limit: int = 500) -> int:
        """Anonymise every soft-deleted account past the purge window. Returns the
        number purged. Best-effort per account: a failure rolls back that one
        account and the sweep continues."""
        days = (
            window_days
            if window_days is not None
            else settings.ACCOUNT_PURGE_WINDOW_DAYS
        )
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        due = await self.repo.find_due(cutoff, limit)
        purged = 0
        for profile in due:
            real_id = profile.user_id
            try:
                await self.purge_profile(profile)
                purged += 1
            except Exception:
                await self.db.rollback()
                logger.exception(
                    "Account purge failed; skipping",
                    extra={"event": "account_purge_failed", "user_id": str(real_id)},
                )
        if purged:
            logger.info(
                "Purged %d deleted account(s)",
                purged,
                extra={"event": "accounts_purged", "count": purged},
            )
        return purged

    async def purge_profile(self, profile: UserProfile) -> uuid.UUID:
        """Anonymise one account in a single transaction. Returns the pseudonym
        the content rows were re-keyed to.

        The GoTrue auth identity (login email/phone — the most sensitive PII)
        lives outside Postgres, so local anonymisation alone leaves it resolvable
        forever. We DELETE it before committing ``purged_at``: if GoTrue is down
        the exception rolls the whole account back (purged_at stays unset) and
        run_due retries it on the next sweep, rather than silently marking it
        purged while the identity survives. ``ignore_missing`` makes that retry
        idempotent if a prior attempt already deleted the identity.
        """
        real_id = profile.user_id
        pseudonym = uuid.uuid4()
        await self.repo.anonymize_user(real_id, pseudonym)
        await gotrue.delete_user(real_id, ignore_missing=True)
        await self.repo.mark_purged(profile, datetime.now(timezone.utc))
        await self.repo.commit()
        return pseudonym
