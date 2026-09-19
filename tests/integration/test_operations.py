"""Request ids and the auth rate limiter."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient

from app.core import rate_limit
from app.core.config import settings
from app.core.logging import REQUEST_ID_HEADER
from app.models import User

UserFactory = Callable[..., Awaitable[User]]


class TestRequestId:
    async def test_every_response_carries_one(self, client: AsyncClient) -> None:
        response = await client.get("/health")

        assert response.headers[REQUEST_ID_HEADER]

    async def test_an_inbound_id_is_preserved(self, client: AsyncClient) -> None:
        """So a trace survives across services rather than restarting here."""
        response = await client.get("/health", headers={REQUEST_ID_HEADER: "trace-abc123"})

        assert response.headers[REQUEST_ID_HEADER] == "trace-abc123"

    async def test_ids_differ_between_requests(self, client: AsyncClient) -> None:
        first = await client.get("/health")
        second = await client.get("/health")

        assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]

    async def test_error_responses_carry_one_too(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/barbershops/not-a-uuid")

        assert response.status_code == 422
        assert response.headers[REQUEST_ID_HEADER]


@pytest.fixture
def rate_limiting_on() -> Any:
    """Enable the limiter for one test, and leave it clean afterwards."""
    original = settings.RATE_LIMIT_ENABLED
    settings.RATE_LIMIT_ENABLED = True
    rate_limit.auth_limiter.reset()
    yield
    settings.RATE_LIMIT_ENABLED = original
    rate_limit.auth_limiter.reset()


class TestRateLimit:
    async def test_repeated_logins_are_eventually_refused(
        self, client: AsyncClient, rate_limiting_on: None
    ) -> None:
        body = {"email": "nobody@example.com", "password": "WrongPass1!"}

        statuses = [
            (await client.post("/api/v1/auth/login", json=body)).status_code
            for _ in range(rate_limit.auth_limiter.limit + 2)
        ]

        assert statuses[0] == 401
        assert statuses[-1] == 429

    async def test_the_refusal_says_how_long_to_wait(
        self, client: AsyncClient, rate_limiting_on: None
    ) -> None:
        body = {"email": "nobody@example.com", "password": "WrongPass1!"}
        for _ in range(rate_limit.auth_limiter.limit + 1):
            response = await client.post("/api/v1/auth/login", json=body)

        assert response.status_code == 429
        error = response.json()["error"]
        assert error["code"] == "RATE_LIMITED"
        assert error["details"]["retry_after_seconds"] > 0

    async def test_other_endpoints_are_not_limited(
        self, client: AsyncClient, rate_limiting_on: None
    ) -> None:
        """The limiter guards credentials, not browsing."""
        for _ in range(rate_limit.auth_limiter.limit + 5):
            response = await client.get("/api/v1/barbershops")

        assert response.status_code == 200

    async def test_it_is_off_by_default_in_this_suite(self, client: AsyncClient) -> None:
        body = {"email": "nobody@example.com", "password": "WrongPass1!"}

        for _ in range(rate_limit.auth_limiter.limit + 3):
            response = await client.post("/api/v1/auth/login", json=body)

        assert response.status_code == 401


class TestLimiterUnit:
    def test_allows_up_to_the_limit(self) -> None:
        limiter = rate_limit.SlidingWindowLimiter(limit=3, window_seconds=60)

        for _ in range(3):
            limiter.check("client")

    def test_refuses_the_next_one(self) -> None:
        limiter = rate_limit.SlidingWindowLimiter(limit=2, window_seconds=60)
        limiter.check("client")
        limiter.check("client")

        with pytest.raises(Exception, match="Too many attempts"):
            limiter.check("client")

    def test_clients_are_counted_separately(self) -> None:
        limiter = rate_limit.SlidingWindowLimiter(limit=1, window_seconds=60)
        limiter.check("first")

        limiter.check("second")

    def test_the_window_expires(self) -> None:
        limiter = rate_limit.SlidingWindowLimiter(limit=1, window_seconds=0)
        limiter.check("client")

        limiter.check("client")
