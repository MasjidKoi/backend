import logging
from typing import Callable

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

# Atomic fixed-window counter: INCR the key and, on the first hit of a window
# (count == 1), set the TTL — all in one round trip so a crash between the two
# commands can never leave a TTL-less key that would pin the counter forever.
# The extra TTL == -1 guard defensively re-applies the window even if a prior
# key somehow lost its expiry (e.g. an INCR that raced an earlier crash).
_INCR_WITH_TTL_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
elseif redis.call('TTL', KEYS[1]) == -1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""

# SECURITY: the rate-limit key is derived from request.client.host, which under
# --proxy-headers reflects the right-most untrusted hop in X-Forwarded-For as
# resolved by uvicorn's --forwarded-allow-ips. That trust boundary MUST be
# pinned to the reverse proxy's address range only (see the Dockerfile CMD): if
# every upstream is trusted (`*`) AND the proxy does not strip/replace inbound
# XFF, a client can spoof the header and rotate through fake IPs to defeat this
# limiter. Keep --forwarded-allow-ips scoped to the Caddy/proxy CIDR.


def make_rate_limiter(
    *, limit: int, window_s: int, key_prefix: str, fail_closed: bool = False
) -> Callable:
    """
    Fixed-window rate limiter backed by Redis INCR + EXPIRE (one atomic Lua call).

    Each client IP gets ``limit`` requests per ``window_s`` seconds; the counter
    resets when the window key expires (a fixed window, NOT a sliding window).

    By default it degrades gracefully — if Redis is unavailable or errors it
    skips limiting rather than blocking (fail-open). Pass ``fail_closed=True`` on
    surfaces where an un-limited flood is more dangerous than a brief outage
    (e.g. the payment IPN/redirect and the public report submission): those raise
    503 when Redis can't be consulted.
    """

    async def _limit(request: Request) -> None:
        redis = getattr(request.app.state, "redis", None)
        if redis is None:
            if fail_closed:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Rate limiter unavailable",
                )
            return

        client_ip = request.client.host if request.client else "unknown"
        key = f"rl:{key_prefix}:{client_ip}"
        try:
            count = await redis.eval(_INCR_WITH_TTL_LUA, 1, key, window_s)
            if count > limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded — max {limit} req per {window_s}s",
                )
        except HTTPException:
            raise
        except Exception as exc:
            if fail_closed:
                logger.warning("Rate limiter Redis error (failing closed): %s", exc)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Rate limiter unavailable",
                ) from exc
            logger.warning("Rate limiter Redis error (skipping): %s", exc)

    return _limit
