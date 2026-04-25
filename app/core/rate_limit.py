import logging
from typing import Callable

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)


def make_rate_limiter(*, limit: int, window_s: int, key_prefix: str) -> Callable:
    """
    Sliding-window rate limiter backed by Redis INCR + EXPIRE.
    Degrades gracefully if Redis is unavailable — skips limiting rather than blocking.
    """

    async def _limit(request: Request) -> None:
        redis = getattr(request.app.state, "redis", None)
        if redis is None:
            return

        client_ip = request.client.host if request.client else "unknown"
        key = f"rl:{key_prefix}:{client_ip}"
        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, window_s)
            if count > limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded — max {limit} req per {window_s}s",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Rate limiter Redis error (skipping): %s", exc)

    return _limit
