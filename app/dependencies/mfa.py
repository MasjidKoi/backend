from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.mfa_service import MfaService


def get_mfa_service(db: AsyncSession = Depends(get_db)) -> MfaService:
    return MfaService(db)
