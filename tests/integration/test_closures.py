"""Shop-wide closures and the manual-confirmation flow (docs/SPEC.md §11.8, §11.12)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, AppointmentStatus, User

Bookable = Callable[..., Awaitable[dict[str, Any]]]
UserFactory = Callable[..., Awaitable[User]]
HeaderFactory = Callable[[User], dict[str, str]]


def closures_url(shop_id: uuid.UUID) -> str:
    return f"/api/v1/barbershops/{shop_id}/closures"


async def slots_on(client: AsyncClient, fx: dict[str, Any], day: datetime) -> list[dict[str, Any]]:
    response = await client.get(
        f"/api/v1/barbers/{fx['barber'].id}/available-slots",
        params={"date": day.date().isoformat(), "service_id": str(fx["service"].id)},
    )
    assert response.status_code == 200, response.text
    slots: list[dict[str, Any]] = response.json()["slots"]
    return slots


def a_week_out() -> datetime:
    return datetime.now(UTC) + timedelta(days=7)


class TestClosures:
    async def test_owner_closes_a_day(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        day = a_week_out().date()

        response = await client.post(
            closures_url(fx["shop"].id),
            json={
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
                "reason": "Public holiday",
            },
            headers=auth_headers(fx["owner"]),
        )

        assert response.status_code == 201
        assert response.json()["reason"] == "Public holiday"

    async def test_a_closed_day_offers_no_slots(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        day = a_week_out()
        before = await slots_on(client, fx, day)
        assert before, "fixture should offer slots before the closure"

        await client.post(
            closures_url(fx["shop"].id),
            json={"start_date": day.date().isoformat(), "end_date": day.date().isoformat()},
            headers=auth_headers(fx["owner"]),
        )

        assert await slots_on(client, fx, day) == []

    async def test_neighbouring_days_are_unaffected(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        day = a_week_out()
        await client.post(
            closures_url(fx["shop"].id),
            json={"start_date": day.date().isoformat(), "end_date": day.date().isoformat()},
            headers=auth_headers(fx["owner"]),
        )

        assert await slots_on(client, fx, day + timedelta(days=1)) != []

    async def test_a_multi_day_closure_covers_its_whole_range(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        """`end_date` is inclusive."""
        fx = await bookable()
        start = a_week_out()
        await client.post(
            closures_url(fx["shop"].id),
            json={
                "start_date": start.date().isoformat(),
                "end_date": (start + timedelta(days=2)).date().isoformat(),
                "reason": "Refit",
            },
            headers=auth_headers(fx["owner"]),
        )

        for offset in range(3):
            assert await slots_on(client, fx, start + timedelta(days=offset)) == [], offset
        assert await slots_on(client, fx, start + timedelta(days=3)) != []

    async def test_booking_on_a_closed_day_is_refused(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        day = a_week_out()
        slot = (await slots_on(client, fx, day))[0]["start"]
        await client.post(
            closures_url(fx["shop"].id),
            json={"start_date": day.date().isoformat(), "end_date": day.date().isoformat()},
            headers=auth_headers(fx["owner"]),
        )

        response = await client.post(
            "/api/v1/appointments",
            json={
                "barber_id": str(fx["barber"].id),
                "service_id": str(fx["service"].id),
                "start_time": slot,
            },
            headers=auth_headers(fx["customer"]),
        )

        assert response.status_code == 409

    async def test_closing_over_booked_time_is_refused(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        """Otherwise a customer holds an appointment on a day the shop is shut."""
        fx = await bookable()
        day = a_week_out()
        start = day.replace(hour=8, minute=0, second=0, microsecond=0)
        db.add(
            Appointment(
                customer_id=fx["customer"].id,
                barber_id=fx["barber"].id,
                service_id=fx["service"].id,
                start_time=start,
                end_time=start + timedelta(minutes=45),
                total_price=Decimal("5000"),
            )
        )
        await db.flush()

        response = await client.post(
            closures_url(fx["shop"].id),
            json={"start_date": day.date().isoformat(), "end_date": day.date().isoformat()},
            headers=auth_headers(fx["owner"]),
        )

        assert response.status_code == 409
        assert response.json()["error"]["details"]["count"] == 1

    async def test_a_cancelled_appointment_does_not_block_a_closure(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        day = a_week_out()
        start = day.replace(hour=8, minute=0, second=0, microsecond=0)
        db.add(
            Appointment(
                customer_id=fx["customer"].id,
                barber_id=fx["barber"].id,
                service_id=fx["service"].id,
                start_time=start,
                end_time=start + timedelta(minutes=45),
                total_price=Decimal("5000"),
                status=AppointmentStatus.CANCELLED,
            )
        )
        await db.flush()

        response = await client.post(
            closures_url(fx["shop"].id),
            json={"start_date": day.date().isoformat(), "end_date": day.date().isoformat()},
            headers=auth_headers(fx["owner"]),
        )

        assert response.status_code == 201

    async def test_deleting_a_closure_restores_availability(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        day = a_week_out()
        headers = auth_headers(fx["owner"])
        created = (
            await client.post(
                closures_url(fx["shop"].id),
                json={"start_date": day.date().isoformat(), "end_date": day.date().isoformat()},
                headers=headers,
            )
        ).json()

        response = await client.delete(f"/api/v1/closures/{created['id']}", headers=headers)

        assert response.status_code == 204
        assert await slots_on(client, fx, day) != []

    async def test_listing_is_public(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        """A customer should see "closed for New Year", not an unexplained empty day."""
        fx = await bookable()
        day = a_week_out()
        await client.post(
            closures_url(fx["shop"].id),
            json={
                "start_date": day.date().isoformat(),
                "end_date": day.date().isoformat(),
                "reason": "New Year",
            },
            headers=auth_headers(fx["owner"]),
        )

        body = (await client.get(closures_url(fx["shop"].id))).json()

        assert [c["reason"] for c in body] == ["New Year"]

    async def test_another_owner_cannot_close_the_shop(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        intruder = await bookable()
        day = a_week_out().date()

        response = await client.post(
            closures_url(fx["shop"].id),
            json={"start_date": day.isoformat(), "end_date": day.isoformat()},
            headers=auth_headers(intruder["owner"]),
        )

        assert response.status_code == 403

    async def test_an_inverted_range_is_rejected(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        day = a_week_out()

        response = await client.post(
            closures_url(fx["shop"].id),
            json={
                "start_date": day.date().isoformat(),
                "end_date": (day - timedelta(days=2)).date().isoformat(),
            },
            headers=auth_headers(fx["owner"]),
        )

        assert response.status_code == 422

    async def test_a_duplicate_closure_conflicts(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        day = a_week_out().date()
        headers = auth_headers(fx["owner"])
        body = {"start_date": day.isoformat(), "end_date": day.isoformat()}
        await client.post(closures_url(fx["shop"].id), json=body, headers=headers)

        response = await client.post(closures_url(fx["shop"].id), json=body, headers=headers)

        assert response.status_code == 409


class TestManualConfirmation:
    async def _requiring_confirmation(
        self, client: AsyncClient, db: AsyncSession, fx: dict[str, Any]
    ) -> None:
        fx["shop"].requires_confirmation = True
        await db.flush()

    async def test_bookings_default_to_confirmed(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        slot = (await slots_on(client, fx, a_week_out()))[0]["start"]

        body = (
            await client.post(
                "/api/v1/appointments",
                json={
                    "barber_id": str(fx["barber"].id),
                    "service_id": str(fx["service"].id),
                    "start_time": slot,
                },
                headers=auth_headers(fx["customer"]),
            )
        ).json()

        assert body["status"] == "CONFIRMED"

    async def test_a_vetting_shop_creates_pending_bookings(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        await self._requiring_confirmation(client, db, fx)
        slot = (await slots_on(client, fx, a_week_out()))[0]["start"]

        body = (
            await client.post(
                "/api/v1/appointments",
                json={
                    "barber_id": str(fx["barber"].id),
                    "service_id": str(fx["service"].id),
                    "start_time": slot,
                },
                headers=auth_headers(fx["customer"]),
            )
        ).json()

        assert body["status"] == "PENDING"

    async def test_a_pending_booking_still_holds_the_slot(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """The shop must not be able to sell the same time twice while deciding."""
        fx = await bookable()
        await self._requiring_confirmation(client, db, fx)
        day = a_week_out()
        slot = (await slots_on(client, fx, day))[0]["start"]
        payload = {
            "barber_id": str(fx["barber"].id),
            "service_id": str(fx["service"].id),
            "start_time": slot,
        }
        await client.post(
            "/api/v1/appointments", json=payload, headers=auth_headers(fx["customer"])
        )

        second = await client.post(
            "/api/v1/appointments", json=payload, headers=auth_headers(await make_user())
        )

        assert second.status_code == 409
        assert slot not in [s["start"] for s in await slots_on(client, fx, day)]

    async def test_staff_confirm_a_pending_booking(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        await self._requiring_confirmation(client, db, fx)
        slot = (await slots_on(client, fx, a_week_out()))[0]["start"]
        created = (
            await client.post(
                "/api/v1/appointments",
                json={
                    "barber_id": str(fx["barber"].id),
                    "service_id": str(fx["service"].id),
                    "start_time": slot,
                },
                headers=auth_headers(fx["customer"]),
            )
        ).json()

        response = await client.patch(
            f"/api/v1/appointments/{created['id']}/confirm",
            headers=auth_headers(fx["barber_user"]),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "CONFIRMED"

    async def test_the_customer_cannot_confirm_their_own_booking(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        await self._requiring_confirmation(client, db, fx)
        slot = (await slots_on(client, fx, a_week_out()))[0]["start"]
        created = (
            await client.post(
                "/api/v1/appointments",
                json={
                    "barber_id": str(fx["barber"].id),
                    "service_id": str(fx["service"].id),
                    "start_time": slot,
                },
                headers=auth_headers(fx["customer"]),
            )
        ).json()

        response = await client.patch(
            f"/api/v1/appointments/{created['id']}/confirm",
            headers=auth_headers(fx["customer"]),
        )

        assert response.status_code == 403

    async def test_an_already_confirmed_booking_cannot_be_confirmed_again(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        slot = (await slots_on(client, fx, a_week_out()))[0]["start"]
        created = (
            await client.post(
                "/api/v1/appointments",
                json={
                    "barber_id": str(fx["barber"].id),
                    "service_id": str(fx["service"].id),
                    "start_time": slot,
                },
                headers=auth_headers(fx["customer"]),
            )
        ).json()

        response = await client.patch(
            f"/api/v1/appointments/{created['id']}/confirm",
            headers=auth_headers(fx["owner"]),
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_TRANSITION"

    async def test_a_customer_can_cancel_a_pending_booking(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        await self._requiring_confirmation(client, db, fx)
        slot = (await slots_on(client, fx, a_week_out()))[0]["start"]
        created = (
            await client.post(
                "/api/v1/appointments",
                json={
                    "barber_id": str(fx["barber"].id),
                    "service_id": str(fx["service"].id),
                    "start_time": slot,
                },
                headers=auth_headers(fx["customer"]),
            )
        ).json()

        response = await client.patch(
            f"/api/v1/appointments/{created['id']}/cancel",
            json={"reason": "Changed my mind"},
            headers=auth_headers(fx["customer"]),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "CANCELLED"

    async def test_the_flag_is_visible_and_settable_on_the_shop(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()

        patched = await client.patch(
            f"/api/v1/barbershops/{fx['shop'].id}",
            json={"requires_confirmation": True},
            headers=auth_headers(fx["owner"]),
        )

        assert patched.status_code == 200
        assert patched.json()["requires_confirmation"] is True
        public = (await client.get(f"/api/v1/barbershops/{fx['shop'].id}")).json()
        assert public["requires_confirmation"] is True
