"""Read access to GoTrue's ``auth.mfa_factors`` table.

GoTrue owns this table (it lives outside the app's own models/migrations), so the
query is intentionally raw SQL — but it belongs in a repository, not the HTTP
handler, so the layering contract holds and the DB coupling has one home
(CODEBASE_AUDIT #13). The parameterised ``:uid`` bind keeps it injection-safe.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class MfaFactorRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_verified_totp(self, user_id: uuid.UUID) -> Sequence:
        """Verified TOTP factors for one user, oldest first."""
        result = await self.db.execute(
            text(
                "SELECT id, status, friendly_name "
                "FROM auth.mfa_factors "
                "WHERE user_id = :uid AND factor_type = 'totp' "
                "AND status = 'verified' "
                "ORDER BY created_at"
            ),
            {"uid": str(user_id)},
        )
        return result.mappings().all()
