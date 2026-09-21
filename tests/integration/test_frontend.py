"""The served client (docs/SPEC.md has no frontend section; this guards the mount).

These do not execute JavaScript — they assert the app is actually served, and
that the handful of API shapes the client reads are still the shapes it gets.
A renamed field would otherwise break the UI silently, with every test green.
"""

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
Bookable = Callable[..., Awaitable[dict[str, Any]]]


class TestServing:
    async def test_root_redirects_to_the_app(self, client: AsyncClient) -> None:
        response = await client.get("/")

        assert response.status_code == 307
        assert response.headers["location"] == "/app/"

    async def test_the_index_is_served(self, client: AsyncClient) -> None:
        response = await client.get("/app/")

        assert response.status_code == 200
        assert "BarberHub" in response.text

    @pytest.mark.parametrize("asset", ["styles.css", "app.js"])
    async def test_assets_are_served(self, client: AsyncClient, asset: str) -> None:
        response = await client.get(f"/app/{asset}")

        assert response.status_code == 200
        assert response.content

    async def test_the_api_still_owns_its_prefix(self, client: AsyncClient) -> None:
        """Mounting static files must not shadow the API."""
        response = await client.get("/api/v1/barbershops")

        assert response.status_code == 200


class TestContract:
    """Every field `frontend/app.js` reads must exist in the response."""

    async def test_shop_list_fields(self, client: AsyncClient, bookable: Bookable) -> None:
        await bookable()

        item = (await client.get("/api/v1/barbershops", params={"limit": 1})).json()["items"][0]

        assert {"id", "name", "city", "rating", "reviews_count"} <= item.keys()

    async def test_barber_fields(self, client: AsyncClient, bookable: Bookable) -> None:
        fx = await bookable()

        barber = (await client.get(f"/api/v1/barbershops/{fx['shop'].id}/barbers")).json()[0]

        assert {
            "id",
            "first_name",
            "last_name",
            "rating",
            "reviews_count",
            "experience_years",
        } <= barber.keys()

    async def test_service_fields(self, client: AsyncClient, bookable: Bookable) -> None:
        fx = await bookable()

        service = (await client.get(f"/api/v1/barbershops/{fx['shop'].id}/services")).json()[0]

        assert {"id", "name", "duration_minutes", "price"} <= service.keys()

    async def test_slot_fields(self, client: AsyncClient, bookable: Bookable) -> None:
        from datetime import UTC, datetime, timedelta

        fx = await bookable()
        day = (datetime.now(UTC) + timedelta(days=3)).date()

        slots = (
            await client.get(
                f"/api/v1/barbers/{fx['barber'].id}/available-slots",
                params={"date": day.isoformat(), "service_id": str(fx["service"].id)},
            )
        ).json()["slots"]

        assert slots, "fixture should offer slots"
        assert {"start", "start_local"} <= slots[0].keys()

    async def test_the_error_envelope_shape_the_client_parses(self, client: AsyncClient) -> None:
        """`app.js` reads error.code, error.message and error.details.fields."""
        response = await client.post("/api/v1/auth/login", json={"email": "x", "password": "y"})

        error = response.json()["error"]
        assert {"code", "message", "details"} <= error.keys()
        assert isinstance(error["details"].get("fields"), list)


class TestSource:
    """Cheap guards against the client drifting from the API it talks to."""

    def test_every_api_path_in_the_client_starts_at_v1(self) -> None:
        source = (FRONTEND / "app.js").read_text()

        assert 'const API = "/api/v1"' in source

    def test_the_client_has_no_hardcoded_host(self) -> None:
        """Same-origin by design — a stray localhost would break any deployment."""
        source = (FRONTEND / "app.js").read_text()

        assert not re.search(r"https?://(localhost|127\.0\.0\.1)", source)

    def test_no_build_artefacts_are_required(self) -> None:
        """The client is three files with no build step; keep it that way."""
        assert not (FRONTEND / "package.json").exists()
        assert {p.name for p in FRONTEND.iterdir()} == {"index.html", "styles.css", "app.js"}
