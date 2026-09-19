"""Working hours and breaks over HTTP (docs/SPEC.md §6)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, Barber, Barbershop, Service, User, UserRole
from app.utils.time import utcnow

UserFactory = Callable[..., Awaitable[User]]
ShopFactory = Callable[..., Awaitable[Barbershop]]
BarberFactory = Callable[..., Awaitable[Barber]]
HeaderFactory = Callable[[User], dict[str, str]]


def hours_url(barber_id: uuid.UUID) -> str:
    return f"/api/v1/barbers/{barber_id}/working-hours"


def breaks_url(barber_id: uuid.UUID) -> str:
    return f"/api/v1/barbers/{barber_id}/breaks"


def slot(weekday: int, start: str, end: str) -> dict[str, Any]:
    return {"weekday": weekday, "start_time": start, "end_time": end}


async def _book(
    db: AsyncSession, barber: Barber, shop: Barbershop, customer: User, start: datetime
) -> Appointment:
    service = Service(
        shop_id=shop.id,
        name=f"Haircut {uuid.uuid4().hex[:6]}",
        duration_minutes=45,
        price=Decimal("5000"),
    )
    db.add(service)
    await db.flush()
    appointment = Appointment(
        customer_id=customer.id,
        barber_id=barber.id,
        service_id=service.id,
        start_time=start,
        end_time=start + timedelta(minutes=45),
        total_price=Decimal("5000"),
    )
    db.add(appointment)
    await db.flush()
    return appointment


class TestWorkingHours:
    async def test_barber_sets_their_own_week(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber_user = await make_user(role=UserRole.BARBER)
        barber = await make_barber(barber_user, shop)

        response = await client.put(
            hours_url(barber.id),
            json={"items": [slot(d, "09:00", "18:00") for d in range(5)]},
            headers=auth_headers(barber_user),
        )

        assert response.status_code == 200
        assert len(response.json()) == 5

    async def test_reading_is_public(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        await client.put(
            hours_url(barber.id),
            json={"items": [slot(0, "09:00", "18:00")]},
            headers=auth_headers(owner),
        )

        response = await client.get(hours_url(barber.id))

        assert response.status_code == 200
        assert response.json()[0]["start_time"] == "09:00:00"

    async def test_results_are_ordered_by_day_then_time(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        await client.put(
            hours_url(barber.id),
            json={
                "items": [
                    slot(2, "09:00", "12:00"),
                    slot(0, "14:00", "19:00"),
                    slot(0, "09:00", "13:00"),
                ]
            },
            headers=auth_headers(owner),
        )

        rows = (await client.get(hours_url(barber.id))).json()

        assert [(r["weekday"], r["start_time"]) for r in rows] == [
            (0, "09:00:00"),
            (0, "14:00:00"),
            (2, "09:00:00"),
        ]

    async def test_put_replaces_the_whole_week(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        headers = auth_headers(owner)
        await client.put(
            hours_url(barber.id),
            json={"items": [slot(d, "09:00", "18:00") for d in range(5)]},
            headers=headers,
        )

        response = await client.put(
            hours_url(barber.id), json={"items": [slot(5, "10:00", "16:00")]}, headers=headers
        )

        assert [r["weekday"] for r in response.json()] == [5]

    async def test_empty_payload_clears_the_week(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        headers = auth_headers(owner)
        await client.put(
            hours_url(barber.id), json={"items": [slot(0, "09:00", "18:00")]}, headers=headers
        )

        response = await client.put(hours_url(barber.id), json={"items": []}, headers=headers)

        assert response.json() == []

    async def test_split_shifts_survive_the_round_trip(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)

        await client.put(
            hours_url(barber.id),
            json={"items": [slot(0, "09:00", "13:00"), slot(0, "14:00", "19:00")]},
            headers=auth_headers(owner),
        )
        rows = (await client.get(hours_url(barber.id))).json()

        assert [(r["start_time"], r["end_time"]) for r in rows] == [
            ("09:00:00", "13:00:00"),
            ("14:00:00", "19:00:00"),
        ]

    async def test_overlapping_shifts_are_rejected(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)

        response = await client.put(
            hours_url(barber.id),
            json={"items": [slot(0, "09:00", "15:00"), slot(0, "14:00", "19:00")]},
            headers=auth_headers(owner),
        )

        assert response.status_code == 422

    async def test_a_colleague_cannot_set_the_schedule(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        colleague = await make_user(role=UserRole.BARBER)
        await make_barber(colleague, shop)

        response = await client.put(
            hours_url(barber.id),
            json={"items": [slot(0, "09:00", "18:00")]},
            headers=auth_headers(colleague),
        )

        assert response.status_code == 403

    async def test_anonymous_cannot_set_the_schedule(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)

        response = await client.put(
            hours_url(barber.id), json={"items": [slot(0, "09:00", "18:00")]}
        )

        assert response.status_code == 401

    async def test_unknown_barber_is_404(self, client: AsyncClient) -> None:
        assert (await client.get(hours_url(uuid.uuid4()))).status_code == 404

    async def test_changing_hours_does_not_touch_existing_appointments(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """A template change must not silently cancel agreed bookings."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        appointment = await _book(db, barber, shop, await make_user(), utcnow() + timedelta(days=2))

        await client.put(hours_url(barber.id), json={"items": []}, headers=auth_headers(owner))
        await db.refresh(appointment)

        assert appointment.status.value == "CONFIRMED"


class TestBreaks:
    async def test_barber_blocks_their_own_time(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber_user = await make_user(role=UserRole.BARBER)
        barber = await make_barber(barber_user, shop)
        start = utcnow() + timedelta(days=1)

        response = await client.post(
            breaks_url(barber.id),
            json={
                "start_datetime": start.isoformat(),
                "end_datetime": (start + timedelta(hours=1)).isoformat(),
                "reason": "Lunch",
            },
            headers=auth_headers(barber_user),
        )

        assert response.status_code == 201
        assert response.json()["reason"] == "Lunch"

    async def test_naive_datetime_is_rejected(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)

        response = await client.post(
            breaks_url(barber.id),
            json={
                "start_datetime": "2026-09-20T09:00:00",
                "end_datetime": "2026-09-20T10:00:00",
            },
            headers=auth_headers(owner),
        )

        assert response.status_code == 422
        assert "timezone offset" in str(response.json()["error"]["details"])

    async def test_break_over_a_booked_appointment_is_refused(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """Otherwise the appointment survives but sits inside blocked time."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        start = utcnow().replace(microsecond=0) + timedelta(days=2)
        appointment = await _book(db, barber, shop, await make_user(), start)

        response = await client.post(
            breaks_url(barber.id),
            json={
                "start_datetime": (start - timedelta(minutes=30)).isoformat(),
                "end_datetime": (start + timedelta(minutes=30)).isoformat(),
            },
            headers=auth_headers(owner),
        )

        assert response.status_code == 409
        assert response.json()["error"]["details"]["conflicting_appointments"] == [
            str(appointment.id)
        ]

    async def test_break_touching_an_appointment_end_is_allowed(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """Half-open ranges: a break may start exactly when the cut ends."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        start = utcnow().replace(microsecond=0) + timedelta(days=2)
        await _book(db, barber, shop, await make_user(), start)

        response = await client.post(
            breaks_url(barber.id),
            json={
                "start_datetime": (start + timedelta(minutes=45)).isoformat(),
                "end_datetime": (start + timedelta(minutes=90)).isoformat(),
            },
            headers=auth_headers(owner),
        )

        assert response.status_code == 201

    async def test_break_over_a_cancelled_appointment_is_allowed(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        start = utcnow().replace(microsecond=0) + timedelta(days=2)
        appointment = await _book(db, barber, shop, await make_user(), start)
        appointment.status = appointment.status.__class__.CANCELLED
        await db.flush()

        response = await client.post(
            breaks_url(barber.id),
            json={
                "start_datetime": start.isoformat(),
                "end_datetime": (start + timedelta(hours=1)).isoformat(),
            },
            headers=auth_headers(owner),
        )

        assert response.status_code == 201

    async def test_listing_is_staff_only(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        customer = await make_user(role=UserRole.CUSTOMER)

        assert (await client.get(breaks_url(barber.id))).status_code == 401
        assert (
            await client.get(breaks_url(barber.id), headers=auth_headers(customer))
        ).status_code == 403

    async def test_listing_filters_by_date_range(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        headers = auth_headers(owner)
        soon = datetime(2026, 10, 1, 9, tzinfo=UTC)
        later = datetime(2026, 12, 1, 9, tzinfo=UTC)
        for start in (soon, later):
            await client.post(
                breaks_url(barber.id),
                json={
                    "start_datetime": start.isoformat(),
                    "end_datetime": (start + timedelta(hours=1)).isoformat(),
                },
                headers=headers,
            )

        rows = (
            await client.get(
                breaks_url(barber.id),
                params={
                    "date_from": datetime(2026, 11, 1, tzinfo=UTC).isoformat(),
                    "date_to": datetime(2027, 1, 1, tzinfo=UTC).isoformat(),
                },
                headers=headers,
            )
        ).json()

        assert len(rows) == 1
        assert rows[0]["start_datetime"].startswith("2026-12-01")

    async def test_delete_removes_the_break(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        headers = auth_headers(owner)
        start = utcnow() + timedelta(days=1)
        created = (
            await client.post(
                breaks_url(barber.id),
                json={
                    "start_datetime": start.isoformat(),
                    "end_datetime": (start + timedelta(hours=1)).isoformat(),
                },
                headers=headers,
            )
        ).json()

        response = await client.delete(f"/api/v1/breaks/{created['id']}", headers=headers)

        assert response.status_code == 204
        assert (await client.get(breaks_url(barber.id), headers=headers)).json() == []

    async def test_a_colleague_cannot_delete_a_break(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        colleague = await make_user(role=UserRole.BARBER)
        await make_barber(colleague, shop)
        start = utcnow() + timedelta(days=1)
        created = (
            await client.post(
                breaks_url(barber.id),
                json={
                    "start_datetime": start.isoformat(),
                    "end_datetime": (start + timedelta(hours=1)).isoformat(),
                },
                headers=auth_headers(owner),
            )
        ).json()

        response = await client.delete(
            f"/api/v1/breaks/{created['id']}", headers=auth_headers(colleague)
        )

        assert response.status_code == 403

    async def test_unknown_break_is_404(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)

        response = await client.delete(
            f"/api/v1/breaks/{uuid.uuid4()}", headers=auth_headers(owner)
        )

        assert response.status_code == 404
