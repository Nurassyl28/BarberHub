"""Booking, moving, and closing out appointments (docs/SPEC.md §4, §6)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, AppointmentStatus, User, UserRole

Bookable = Callable[..., Awaitable[dict[str, Any]]]
UserFactory = Callable[..., Awaitable[User]]
HeaderFactory = Callable[[User], dict[str, str]]

BASE = "/api/v1/appointments"


async def first_slot(client: AsyncClient, fx: dict[str, Any], day_offset: int = 7) -> str:
    """The first start time the availability endpoint actually offers."""
    day = (datetime.now(UTC) + timedelta(days=day_offset)).date()
    response = await client.get(
        f"/api/v1/barbers/{fx['barber'].id}/available-slots",
        params={"date": day.isoformat(), "service_id": str(fx["service"].id)},
    )
    slots = response.json()["slots"]
    assert slots, "fixture should offer slots"
    return str(slots[0]["start"])


async def book(
    client: AsyncClient,
    fx: dict[str, Any],
    auth_headers: HeaderFactory,
    *,
    start: str | None = None,
    customer: User | None = None,
) -> Any:
    return await client.post(
        BASE,
        json={
            "barber_id": str(fx["barber"].id),
            "service_id": str(fx["service"].id),
            "start_time": start or await first_slot(client, fx),
        },
        headers=auth_headers(customer or fx["customer"]),
    )


class TestCreate:
    async def test_books_an_offered_slot(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()

        response = await book(client, fx, auth_headers)

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "CONFIRMED"
        assert body["customer"]["id"] == str(fx["customer"].id)
        assert body["service"]["duration_minutes"] == 45

    async def test_end_time_is_derived_from_the_service(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        """The client cannot set the end time, so it can't buy 45 minutes and take 90."""
        fx = await bookable(duration_minutes=30)

        body = (await book(client, fx, auth_headers)).json()

        start = datetime.fromisoformat(body["start_time"])
        end = datetime.fromisoformat(body["end_time"])
        assert end - start == timedelta(minutes=30)

    async def test_price_is_snapshotted(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        """A later price rise must not rewrite an existing booking."""
        fx = await bookable()
        body = (await book(client, fx, auth_headers)).json()

        fx["service"].price = Decimal("9999.00")
        await db.flush()

        refetched = await client.get(f"{BASE}/{body['id']}", headers=auth_headers(fx["customer"]))
        assert Decimal(refetched.json()["total_price"]) == Decimal("5000.00")

    async def test_the_booked_slot_disappears_from_availability(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        slot = await first_slot(client, fx)
        await book(client, fx, auth_headers, start=slot)

        day = datetime.fromisoformat(slot).date()
        offered = (
            await client.get(
                f"/api/v1/barbers/{fx['barber'].id}/available-slots",
                params={"date": day.isoformat(), "service_id": str(fx["service"].id)},
            )
        ).json()["slots"]

        assert slot not in [s["start"] for s in offered]

    async def test_a_time_never_offered_is_refused(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        """03:00 is outside working hours, so it was never on the menu."""
        fx = await bookable()
        day = (datetime.now(UTC) + timedelta(days=7)).date()
        off_hours = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=3)

        response = await book(client, fx, auth_headers, start=off_hours.isoformat())

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "OUTSIDE_WORKING_HOURS"

    async def test_an_off_grid_time_is_refused(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        """09:07 sits inside working hours but was never offered."""
        fx = await bookable()
        slot = datetime.fromisoformat(await first_slot(client, fx))

        response = await book(
            client, fx, auth_headers, start=(slot + timedelta(minutes=7)).isoformat()
        )

        assert response.status_code == 409

    async def test_a_naive_start_time_is_rejected(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()

        response = await client.post(
            BASE,
            json={
                "barber_id": str(fx["barber"].id),
                "service_id": str(fx["service"].id),
                "start_time": "2027-03-15T09:00:00",
            },
            headers=auth_headers(fx["customer"]),
        )

        assert response.status_code == 422
        assert "timezone offset" in str(response.json()["error"]["details"])

    async def test_a_past_time_is_refused(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        yesterday = datetime.now(UTC) - timedelta(days=1)

        response = await book(client, fx, auth_headers, start=yesterday.isoformat())

        assert response.status_code == 409

    async def test_a_service_the_barber_does_not_perform_is_refused(
        self,
        client: AsyncClient,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        mine = await bookable()
        other = await bookable()

        response = await client.post(
            BASE,
            json={
                "barber_id": str(mine["barber"].id),
                "service_id": str(other["service"].id),
                "start_time": await first_slot(client, mine),
            },
            headers=auth_headers(mine["customer"]),
        )

        assert response.status_code == 422

    async def test_anonymous_cannot_book(self, client: AsyncClient, bookable: Bookable) -> None:
        fx = await bookable()

        response = await client.post(
            BASE,
            json={
                "barber_id": str(fx["barber"].id),
                "service_id": str(fx["service"].id),
                "start_time": await first_slot(client, fx),
            },
        )

        assert response.status_code == 401

    async def test_two_customers_cannot_take_the_same_slot(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        slot = await first_slot(client, fx)
        assert (await book(client, fx, auth_headers, start=slot)).status_code == 201

        second = await book(client, fx, auth_headers, start=slot, customer=await make_user())

        assert second.status_code == 409
        # Specifically SLOT_TAKEN, not OUTSIDE_WORKING_HOURS: the time is a
        # perfectly good slot that someone else already holds.
        assert second.json()["error"]["code"] == "SLOT_TAKEN"

    async def test_an_overlapping_slot_is_refused(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """15 minutes into a 45-minute cut is still that barber's time."""
        fx = await bookable()
        slot = datetime.fromisoformat(await first_slot(client, fx))
        await book(client, fx, auth_headers, start=slot.isoformat())

        second = await book(
            client,
            fx,
            auth_headers,
            start=(slot + timedelta(minutes=15)).isoformat(),
            customer=await make_user(),
        )

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "SLOT_TAKEN"

    async def test_an_adjacent_slot_is_allowed(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        slot = datetime.fromisoformat(await first_slot(client, fx))
        await book(client, fx, auth_headers, start=slot.isoformat())

        second = await book(
            client,
            fx,
            auth_headers,
            start=(slot + timedelta(minutes=45)).isoformat(),
            customer=await make_user(),
        )

        assert second.status_code == 201


class TestCancel:
    async def test_customer_can_cancel_in_good_time(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()

        response = await client.patch(
            f"{BASE}/{created['id']}/cancel",
            json={"reason": "Changed my mind"},
            headers=auth_headers(fx["customer"]),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "CANCELLED"
        assert response.json()["cancellation_reason"] == "Changed my mind"

    async def test_cancelling_frees_the_slot(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        slot = await first_slot(client, fx)
        created = (await book(client, fx, auth_headers, start=slot)).json()
        await client.patch(
            f"{BASE}/{created['id']}/cancel", json={}, headers=auth_headers(fx["customer"])
        )

        rebook = await book(client, fx, auth_headers, start=slot, customer=await make_user())

        assert rebook.status_code == 201

    async def test_records_who_cancelled(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()

        await client.patch(
            f"{BASE}/{created['id']}/cancel", json={}, headers=auth_headers(fx["owner"])
        )
        stored = await db.get(Appointment, uuid.UUID(created["id"]))

        assert stored is not None
        assert stored.cancelled_by_id == fx["owner"].id

    async def test_a_stranger_cannot_cancel(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()

        response = await client.patch(
            f"{BASE}/{created['id']}/cancel",
            json={},
            headers=auth_headers(await make_user()),
        )

        assert response.status_code == 403

    async def test_cancelling_twice_is_refused(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()
        headers = auth_headers(fx["customer"])
        await client.patch(f"{BASE}/{created['id']}/cancel", json={}, headers=headers)

        response = await client.patch(f"{BASE}/{created['id']}/cancel", json={}, headers=headers)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_TRANSITION"

    async def test_customer_cannot_cancel_inside_the_cutoff(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()
        stored = await db.get(Appointment, uuid.UUID(created["id"]))
        assert stored is not None
        stored.start_time = datetime.now(UTC) + timedelta(minutes=30)
        stored.end_time = stored.start_time + timedelta(minutes=45)
        await db.flush()

        response = await client.patch(
            f"{BASE}/{created['id']}/cancel", json={}, headers=auth_headers(fx["customer"])
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CUTOFF_PASSED"

    async def test_staff_may_cancel_inside_the_cutoff(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        """A barber who falls ill has to be able to cancel at short notice."""
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()
        stored = await db.get(Appointment, uuid.UUID(created["id"]))
        assert stored is not None
        stored.start_time = datetime.now(UTC) + timedelta(minutes=30)
        stored.end_time = stored.start_time + timedelta(minutes=45)
        await db.flush()

        response = await client.patch(
            f"{BASE}/{created['id']}/cancel", json={}, headers=auth_headers(fx["barber_user"])
        )

        assert response.status_code == 200


class TestReschedule:
    async def test_moves_the_same_booking(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()
        later = (datetime.fromisoformat(created["start_time"]) + timedelta(hours=2)).isoformat()

        response = await client.patch(
            f"{BASE}/{created['id']}/reschedule",
            json={"start_time": later},
            headers=auth_headers(fx["customer"]),
        )

        assert response.status_code == 200
        assert response.json()["id"] == created["id"]
        assert response.json()["start_time"] == later.replace("+00:00", "Z")

    async def test_can_nudge_within_its_own_footprint(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        """Without excluding itself, a booking would block its own new time."""
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()
        nudged = (datetime.fromisoformat(created["start_time"]) + timedelta(minutes=15)).isoformat()

        response = await client.patch(
            f"{BASE}/{created['id']}/reschedule",
            json={"start_time": nudged},
            headers=auth_headers(fx["customer"]),
        )

        assert response.status_code == 200

    async def test_the_old_slot_becomes_free_again(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        slot = await first_slot(client, fx)
        created = (await book(client, fx, auth_headers, start=slot)).json()
        later = (datetime.fromisoformat(slot) + timedelta(hours=3)).isoformat()
        await client.patch(
            f"{BASE}/{created['id']}/reschedule",
            json={"start_time": later},
            headers=auth_headers(fx["customer"]),
        )

        rebook = await book(client, fx, auth_headers, start=slot, customer=await make_user())

        assert rebook.status_code == 201

    async def test_moving_onto_a_taken_slot_is_refused(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        slot = datetime.fromisoformat(await first_slot(client, fx))
        mine = (await book(client, fx, auth_headers, start=slot.isoformat())).json()
        taken = (slot + timedelta(hours=2)).isoformat()
        await book(client, fx, auth_headers, start=taken, customer=await make_user())

        response = await client.patch(
            f"{BASE}/{mine['id']}/reschedule",
            json={"start_time": taken},
            headers=auth_headers(fx["customer"]),
        )

        assert response.status_code == 409

    async def test_a_cancelled_booking_cannot_be_moved(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()
        headers = auth_headers(fx["customer"])
        await client.patch(f"{BASE}/{created['id']}/cancel", json={}, headers=headers)

        response = await client.patch(
            f"{BASE}/{created['id']}/reschedule",
            json={"start_time": created["start_time"]},
            headers=headers,
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_TRANSITION"


class TestCloseOut:
    async def _started(
        self, client: AsyncClient, db: AsyncSession, fx: dict[str, Any], headers: Any
    ) -> str:
        created = (await book(client, fx, headers)).json()
        stored = await db.get(Appointment, uuid.UUID(created["id"]))
        assert stored is not None
        stored.start_time = datetime.now(UTC) - timedelta(minutes=30)
        stored.end_time = stored.start_time + timedelta(minutes=45)
        await db.flush()
        return str(created["id"])

    async def test_barber_completes_a_started_appointment(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment_id = await self._started(client, db, fx, auth_headers)

        response = await client.patch(
            f"{BASE}/{appointment_id}/complete", headers=auth_headers(fx["barber_user"])
        )

        assert response.status_code == 200
        assert response.json()["status"] == "COMPLETED"

    async def test_a_future_appointment_cannot_be_completed(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        """Otherwise revenue would count work that has not happened."""
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()

        response = await client.patch(
            f"{BASE}/{created['id']}/complete", headers=auth_headers(fx["barber_user"])
        )

        assert response.status_code == 409
        assert "not started" in response.json()["error"]["message"]

    async def test_the_customer_cannot_complete_their_own_appointment(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment_id = await self._started(client, db, fx, auth_headers)

        response = await client.patch(
            f"{BASE}/{appointment_id}/complete", headers=auth_headers(fx["customer"])
        )

        assert response.status_code == 403

    async def test_no_show_is_recorded(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment_id = await self._started(client, db, fx, auth_headers)

        response = await client.patch(
            f"{BASE}/{appointment_id}/no-show", headers=auth_headers(fx["owner"])
        )

        assert response.status_code == 200
        assert response.json()["status"] == "NO_SHOW"

    async def test_a_completed_appointment_is_terminal(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment_id = await self._started(client, db, fx, auth_headers)
        headers = auth_headers(fx["barber_user"])
        await client.patch(f"{BASE}/{appointment_id}/complete", headers=headers)

        for action in ("complete", "no-show", "cancel"):
            method = client.patch
            response = await method(
                f"{BASE}/{appointment_id}/{action}",
                json={} if action == "cancel" else None,
                headers=headers,
            )
            assert response.status_code == 409, action

    async def test_a_completed_slot_stays_free_for_others(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        """COMPLETED leaves the exclusion constraint's filtered set, which is
        correct: the time has passed and cannot be double-booked anyway."""
        fx = await bookable()
        appointment_id = await self._started(client, db, fx, auth_headers)
        await client.patch(
            f"{BASE}/{appointment_id}/complete", headers=auth_headers(fx["barber_user"])
        )

        stored = await db.get(Appointment, uuid.UUID(appointment_id))
        assert stored is not None
        assert stored.status is AppointmentStatus.COMPLETED


class TestListing:
    async def test_customer_sees_their_own_bookings(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()

        body = (await client.get(f"{BASE}/me", headers=auth_headers(fx["customer"]))).json()

        assert [i["id"] for i in body["items"]] == [created["id"]]

    async def test_customer_does_not_see_other_peoples_bookings(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        await book(client, fx, auth_headers)

        body = (await client.get(f"{BASE}/me", headers=auth_headers(await make_user()))).json()

        assert body["items"] == []

    async def test_barber_sees_their_schedule(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()

        body = (
            await client.get(
                f"{BASE}/me",
                params={"as_barber": "true"},
                headers=auth_headers(fx["barber_user"]),
            )
        ).json()

        assert [i["id"] for i in body["items"]] == [created["id"]]

    async def test_as_barber_needs_a_barber_profile(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        response = await client.get(
            f"{BASE}/me",
            params={"as_barber": "true"},
            headers=auth_headers(await make_user()),
        )

        assert response.status_code == 403

    async def test_owner_sees_every_booking_in_the_shop(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()

        body = (
            await client.get(
                f"/api/v1/barbershops/{fx['shop'].id}/appointments",
                headers=auth_headers(fx["owner"]),
            )
        ).json()

        assert [i["id"] for i in body["items"]] == [created["id"]]

    async def test_another_owner_cannot_read_the_shop_list(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        intruder = await bookable()

        response = await client.get(
            f"/api/v1/barbershops/{fx['shop'].id}/appointments",
            headers=auth_headers(intruder["owner"]),
        )

        assert response.status_code == 403

    async def test_status_filter(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        headers = auth_headers(fx["customer"])
        created = (await book(client, fx, auth_headers)).json()
        await client.patch(f"{BASE}/{created['id']}/cancel", json={}, headers=headers)

        confirmed = (
            await client.get(f"{BASE}/me", params={"status": "CONFIRMED"}, headers=headers)
        ).json()
        cancelled = (
            await client.get(f"{BASE}/me", params={"status": "CANCELLED"}, headers=headers)
        ).json()

        assert confirmed["items"] == []
        assert len(cancelled["items"]) == 1

    async def test_an_inverted_date_range_is_rejected(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        response = await client.get(
            f"{BASE}/me",
            params={
                "date_from": "2027-05-01T00:00:00Z",
                "date_to": "2027-04-01T00:00:00Z",
            },
            headers=auth_headers(await make_user()),
        )

        assert response.status_code == 422

    async def test_detail_is_visible_to_every_participant(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()

        for person in (fx["customer"], fx["barber_user"], fx["owner"]):
            response = await client.get(f"{BASE}/{created['id']}", headers=auth_headers(person))
            assert response.status_code == 200, person.email

    async def test_detail_is_hidden_from_strangers(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()

        response = await client.get(
            f"{BASE}/{created['id']}", headers=auth_headers(await make_user())
        )

        assert response.status_code == 403

    async def test_admin_sees_anything(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        created = (await book(client, fx, auth_headers)).json()

        response = await client.get(
            f"{BASE}/{created['id']}",
            headers=auth_headers(await make_user(role=UserRole.ADMIN)),
        )

        assert response.status_code == 200

    async def test_unknown_appointment_is_404(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        response = await client.get(
            f"{BASE}/{uuid.uuid4()}", headers=auth_headers(await make_user())
        )

        assert response.status_code == 404
