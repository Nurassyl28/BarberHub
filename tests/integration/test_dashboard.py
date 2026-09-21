"""Dashboard aggregates (docs/SPEC.md §8)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, AppointmentStatus, Barber, Barbershop, Service, User, UserRole

Bookable = Callable[..., Awaitable[dict[str, Any]]]
UserFactory = Callable[..., Awaitable[User]]
BarberFactory = Callable[..., Awaitable[Barber]]
HeaderFactory = Callable[[User], dict[str, str]]

# Mid-month so "this month" never straddles a boundary while the test runs.
NOW = datetime.now(UTC).replace(day=15, hour=12, minute=0, second=0, microsecond=0)


@pytest.fixture
def add_appointment(db: AsyncSession) -> Callable[..., Awaitable[Appointment]]:
    async def build(
        fx: dict[str, Any],
        *,
        status: AppointmentStatus = AppointmentStatus.COMPLETED,
        price: str = "5000.00",
        start: datetime | None = None,
        barber: Barber | None = None,
        service: Service | None = None,
        customer: User | None = None,
    ) -> Appointment:
        moment = start or NOW - timedelta(days=1)
        appointment = Appointment(
            customer_id=(customer or fx["customer"]).id,
            barber_id=(barber or fx["barber"]).id,
            service_id=(service or fx["service"]).id,
            start_time=moment,
            end_time=moment + timedelta(minutes=45),
            total_price=Decimal(price),
            status=status,
        )
        db.add(appointment)
        await db.flush()
        return appointment

    return build


def shop_url(shop: Barbershop) -> str:
    return f"/api/v1/dashboard/barbershop/{shop.id}"


class TestAccess:
    async def test_owner_can_read(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()

        response = await client.get(shop_url(fx["shop"]), headers=auth_headers(fx["owner"]))

        assert response.status_code == 200

    async def test_admin_can_read(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()

        response = await client.get(
            shop_url(fx["shop"]), headers=auth_headers(await make_user(role=UserRole.ADMIN))
        )

        assert response.status_code == 200

    async def test_another_owner_cannot_read(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()
        intruder = await bookable()

        response = await client.get(shop_url(fx["shop"]), headers=auth_headers(intruder["owner"]))

        assert response.status_code == 403

    async def test_the_barber_cannot_read_the_shop_dashboard(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        """Revenue across the shop is the owner's business, not staff's."""
        fx = await bookable()

        response = await client.get(shop_url(fx["shop"]), headers=auth_headers(fx["barber_user"]))

        assert response.status_code == 403

    async def test_anonymous_is_rejected(self, client: AsyncClient, bookable: Bookable) -> None:
        fx = await bookable()

        assert (await client.get(shop_url(fx["shop"]))).status_code == 401

    async def test_unknown_shop_is_404(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        response = await client.get(
            f"/api/v1/dashboard/barbershop/{uuid.uuid4()}",
            headers=auth_headers(await make_user(role=UserRole.SHOP_OWNER)),
        )

        assert response.status_code == 404


class TestCounts:
    async def test_an_empty_shop_reports_zeroes(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        fx = await bookable()

        body = (await client.get(shop_url(fx["shop"]), headers=auth_headers(fx["owner"]))).json()

        assert body["total_appointments"] == 0
        assert Decimal(body["monthly_revenue"]) == Decimal("0.00")
        assert body["most_popular_service"] is None
        assert body["top_barber"] is None

    async def test_each_status_is_counted_separately(
        self,
        client: AsyncClient,
        bookable: Bookable,
        add_appointment: Callable[..., Awaitable[Appointment]],
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        plan = [
            (AppointmentStatus.COMPLETED, 3),
            (AppointmentStatus.CANCELLED, 2),
            (AppointmentStatus.NO_SHOW, 1),
            (AppointmentStatus.CONFIRMED, 4),
        ]
        offset = 0
        for status, count in plan:
            for _ in range(count):
                offset += 1
                await add_appointment(
                    fx, status=status, start=NOW - timedelta(days=1, hours=offset)
                )

        body = (await client.get(shop_url(fx["shop"]), headers=auth_headers(fx["owner"]))).json()

        assert body["completed_appointments"] == 3
        assert body["cancelled_appointments"] == 2
        assert body["no_show_appointments"] == 1
        assert body["total_appointments"] == 10

    async def test_today_counts_only_todays_appointments(
        self,
        client: AsyncClient,
        bookable: Bookable,
        add_appointment: Callable[..., Awaitable[Appointment]],
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        now = datetime.now(UTC)
        await add_appointment(fx, start=now.replace(hour=10, minute=0))
        await add_appointment(fx, start=now.replace(hour=14, minute=0))
        await add_appointment(fx, start=now - timedelta(days=3))

        body = (await client.get(shop_url(fx["shop"]), headers=auth_headers(fx["owner"]))).json()

        assert body["today_appointments"] == 2


class TestRevenue:
    async def test_only_completed_appointments_earn(
        self,
        client: AsyncClient,
        bookable: Bookable,
        add_appointment: Callable[..., Awaitable[Appointment]],
        auth_headers: HeaderFactory,
    ) -> None:
        """A cancelled booking produced no money, whatever it was worth."""
        fx = await bookable()
        await add_appointment(fx, status=AppointmentStatus.COMPLETED, price="5000.00")
        await add_appointment(
            fx,
            status=AppointmentStatus.CANCELLED,
            price="9000.00",
            start=NOW - timedelta(days=2),
        )
        await add_appointment(
            fx,
            status=AppointmentStatus.NO_SHOW,
            price="9000.00",
            start=NOW - timedelta(days=3),
        )
        await add_appointment(
            fx,
            status=AppointmentStatus.CONFIRMED,
            price="9000.00",
            start=NOW + timedelta(days=2),
        )

        body = (await client.get(shop_url(fx["shop"]), headers=auth_headers(fx["owner"]))).json()

        assert Decimal(body["monthly_revenue"]) == Decimal("5000.00")

    async def test_revenue_sums_the_snapshotted_prices(
        self,
        client: AsyncClient,
        bookable: Bookable,
        add_appointment: Callable[..., Awaitable[Appointment]],
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        for offset, price in enumerate(("5000.00", "3000.00", "7000.00")):
            await add_appointment(fx, price=price, start=NOW - timedelta(days=1, hours=offset))

        body = (await client.get(shop_url(fx["shop"]), headers=auth_headers(fx["owner"]))).json()

        assert Decimal(body["monthly_revenue"]) == Decimal("15000.00")
        assert Decimal(body["average_ticket"]) == Decimal("5000.00")

    async def test_average_ticket_is_zero_without_earnings(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        """Division by a zero count must not blow up the whole dashboard."""
        fx = await bookable()

        body = (await client.get(shop_url(fx["shop"]), headers=auth_headers(fx["owner"]))).json()

        assert Decimal(body["average_ticket"]) == Decimal("0.00")

    async def test_a_date_range_narrows_the_numbers(
        self,
        client: AsyncClient,
        bookable: Bookable,
        add_appointment: Callable[..., Awaitable[Appointment]],
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        inside = datetime(2027, 5, 10, 12, tzinfo=UTC)
        outside = datetime(2027, 6, 10, 12, tzinfo=UTC)
        await add_appointment(fx, start=inside, price="5000.00")
        await add_appointment(fx, start=outside, price="9000.00")

        body = (
            await client.get(
                shop_url(fx["shop"]),
                params={
                    "date_from": "2027-05-01T00:00:00Z",
                    "date_to": "2027-06-01T00:00:00Z",
                },
                headers=auth_headers(fx["owner"]),
            )
        ).json()

        assert Decimal(body["monthly_revenue"]) == Decimal("5000.00")
        assert body["total_appointments"] == 1


class TestLeaders:
    async def test_the_most_booked_service_wins(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        add_appointment: Callable[..., Awaitable[Appointment]],
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        beard = Service(
            shop_id=fx["shop"].id,
            name="Beard Trim",
            duration_minutes=30,
            price=Decimal("3000"),
        )
        db.add(beard)
        await db.flush()

        for offset in range(3):
            await add_appointment(fx, start=NOW - timedelta(days=1, hours=offset))
        await add_appointment(fx, service=beard, start=NOW - timedelta(days=2))

        body = (await client.get(shop_url(fx["shop"]), headers=auth_headers(fx["owner"]))).json()

        assert body["most_popular_service"]["id"] == str(fx["service"].id)
        assert body["most_popular_service"]["count"] == 3

    async def test_cancelled_bookings_do_not_make_a_service_popular(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        add_appointment: Callable[..., Awaitable[Appointment]],
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        beard = Service(
            shop_id=fx["shop"].id,
            name="Beard Trim",
            duration_minutes=30,
            price=Decimal("3000"),
        )
        db.add(beard)
        await db.flush()

        for offset in range(5):
            await add_appointment(
                fx,
                service=beard,
                status=AppointmentStatus.CANCELLED,
                start=NOW - timedelta(days=1, hours=offset),
            )
        await add_appointment(fx, start=NOW - timedelta(days=2))

        body = (await client.get(shop_url(fx["shop"]), headers=auth_headers(fx["owner"]))).json()

        assert body["most_popular_service"]["id"] == str(fx["service"].id)

    async def test_the_top_barber_is_ranked_by_revenue(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        make_barber: BarberFactory,
        add_appointment: Callable[..., Awaitable[Appointment]],
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        rival = await make_barber(await make_user(role=UserRole.BARBER), fx["shop"])

        # Fewer appointments, more money.
        await add_appointment(fx, price="20000.00", start=NOW - timedelta(days=1))
        for offset in range(3):
            await add_appointment(
                fx, barber=rival, price="1000.00", start=NOW - timedelta(days=2, hours=offset)
            )

        body = (await client.get(shop_url(fx["shop"]), headers=auth_headers(fx["owner"]))).json()

        assert body["top_barber"]["id"] == str(fx["barber"].id)
        assert Decimal(body["top_barber"]["revenue"]) == Decimal("20000.00")
        assert body["top_barber"]["completed"] == 1

    async def test_another_shops_numbers_do_not_leak_in(
        self,
        client: AsyncClient,
        bookable: Bookable,
        add_appointment: Callable[..., Awaitable[Appointment]],
        auth_headers: HeaderFactory,
    ) -> None:
        mine = await bookable()
        theirs = await bookable()
        await add_appointment(theirs, price="99999.00")

        body = (
            await client.get(shop_url(mine["shop"]), headers=auth_headers(mine["owner"]))
        ).json()

        assert body["total_appointments"] == 0
        assert Decimal(body["monthly_revenue"]) == Decimal("0.00")


class TestBarberDashboard:
    async def test_barber_sees_their_own_numbers(
        self,
        client: AsyncClient,
        bookable: Bookable,
        add_appointment: Callable[..., Awaitable[Appointment]],
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        await add_appointment(fx, price="5000.00")

        body = (
            await client.get("/api/v1/dashboard/barber/me", headers=auth_headers(fx["barber_user"]))
        ).json()

        assert body["barber_id"] == str(fx["barber"].id)
        assert body["completed_appointments"] == 1
        assert Decimal(body["revenue"]) == Decimal("5000.00")

    async def test_a_colleagues_work_is_excluded(
        self,
        client: AsyncClient,
        bookable: Bookable,
        make_user: UserFactory,
        make_barber: BarberFactory,
        add_appointment: Callable[..., Awaitable[Appointment]],
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        rival = await make_barber(await make_user(role=UserRole.BARBER), fx["shop"])
        await add_appointment(fx, barber=rival, price="9000.00")

        body = (
            await client.get("/api/v1/dashboard/barber/me", headers=auth_headers(fx["barber_user"]))
        ).json()

        assert Decimal(body["revenue"]) == Decimal("0.00")

    async def test_a_non_barber_is_404(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        response = await client.get(
            "/api/v1/dashboard/barber/me", headers=auth_headers(await make_user())
        )

        assert response.status_code == 404


class TestPeriod:
    async def test_the_default_period_is_the_shops_current_month(
        self, client: AsyncClient, bookable: Bookable, auth_headers: HeaderFactory
    ) -> None:
        """Month boundaries are shop-local: a shop in Almaty closes its books on
        the 31st local, which is five hours before the 31st UTC."""
        fx = await bookable(timezone="Asia/Almaty")

        body = (await client.get(shop_url(fx["shop"]), headers=auth_headers(fx["owner"]))).json()

        assert body["period"]["timezone"] == "Asia/Almaty"
        assert body["period"]["date_from"].endswith(("+05:00", "Z"))
        start = datetime.fromisoformat(body["period"]["date_from"])
        assert start.day == 1
