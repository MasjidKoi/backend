"""MFA (TOTP) read helpers for the auth router.

Thin service over MfaFactorRepository so the route stays HTTP-only and returns a
validated schema instead of a hand-built dict (CODEBASE_AUDIT #13).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.mfa_factor_repository import MfaFactorRepository
from app.schemas.auth import MfaFactor, MfaFactorsResponse


class MfaService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = MfaFactorRepository(db)

    async def list_factors(self, user_id: uuid.UUID) -> MfaFactorsResponse:
        rows = await self.repo.list_verified_totp(user_id)
        return MfaFactorsResponse(
            factors=[
                MfaFactor(
                    id=str(r["id"]),
                    status=r["status"],
                    friendly_name=r["friendly_name"],
                )
                for r in rows
            ]
        )
