"""A small in-process rate limiter for the auth endpoints.

Deliberately simple: a fixed window per client, held in memory. That is enough
to blunt credential stuffing against a single instance and costs no
infrastructure. It is *not* a distributed limiter — behind several workers each
keeps its own counter, so the effective limit multiplies by the worker count.
Redis is the upgrade path when that matters.
"""

import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.errors import RateLimitedError


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        hits = self._hits[key]

        while hits and now - hits[0] > self.window:
            hits.popleft()

        if len(hits) >= self.limit:
            retry_after = int(self.window - (now - hits[0])) + 1
            raise RateLimitedError(
                "Too many attempts; please wait before trying again",
                details={"retry_after_seconds": retry_after},
            )

        hits.append(now)

    def reset(self) -> None:
        self._hits.clear()


#: Login and registration only. Generous enough that a person fumbling their
#: password is never affected.
auth_limiter = SlidingWindowLimiter(limit=10, window_seconds=60)


def client_key(request: Request) -> str:
    # A proxy header is only trusted when one is present; otherwise the socket
    # address is the honest answer.
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def limit_auth(request: Request) -> None:
    from app.core.config import settings

    if settings.RATE_LIMIT_ENABLED:
        auth_limiter.check(client_key(request))
