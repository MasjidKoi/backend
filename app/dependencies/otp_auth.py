from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.otp_auth_service import OtpAuthService


def get_otp_auth_service(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OtpAuthService:
    # Redis is attached to app.state in the lifespan handler; may be absent in
    # degraded conditions, in which case the service skips policy enforcement.
    redis = getattr(request.app.state, "redis", None)
    return OtpAuthService(db, redis)
