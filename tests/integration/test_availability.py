"""The free-slot endpoint end to end (docs/SPEC.md §5)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Appointment,
    AppointmentStatus,
    Barber,
    BarberBreak,
    Barbershop,
    Service,
    User,
    UserRole,
    WorkingHours,
    barber_services,
)

UserFactory = Callable[..., Awaitable[User]]
ShopFactory = Callable[..., Awaitable[Barbershop]]
BarberFactory = Callable[..., Awaitable[Barber]]

# A Monday far enough out that the 30-minute booking lead never hides slots.
MONDAY = date(2027, 3, 15)


def url(barber_id: uuid.UUID) -> str:
    return f"/api/v1/barbers/{barber_id}/available-slots"


class Fixture:
    def __init__(self, shop: Barbershop, barber: Barber, service: Service) -> None:
        self.shop = shop
        self.barber = barber
        self.service = service


@pytest.fixture
async def booked(
    db: AsyncSession,
    make_user: UserFactory,
    make_shop: ShopFactory,
    make_barber: BarberFactory,
) -> Callable[..., Awaitable[Fixture]]:
    """A barber with Monday 09:00-18:00 and one 45-minute service assigned."""

    async def build(
        timezone: str = "Asia/Almaty",
        duration_minutes: int = 45,
        shifts: list[tuple[time, time]] | None = None,
        weekday: int = MONDAY.weekday(),
    ) -> Fixture:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner, timezone=timezone)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        service = Service(
            shop_id=shop.id,
            name=f"Haircut {uuid.uuid4().hex[:6]}",
            duration_minutes=duration_minutes,
            price=Decimal("5000"),
        )
        db.add(service)
        await db.flush()
        # Insert the link row directly: assigning `barber.services` would lazy-load
        # the existing collection, which an async session cannot do outside a
        # greenlet. Application code avoids this by eager-loading first.
        await db.execute(
            barber_services.insert().values(barber_id=barber.id, service_id=service.id)
        )
        for start, end in shifts if shifts is not None else [(time(9), time(18))]:
            db.add(
                WorkingHours(barber_id=barber.id, weekday=weekday, start_time=start, end_time=end)
            )
        await db.flush()
        return Fixture(shop, barber, service)

    return build


async def fetch(
    client: AsyncClient, fx: Fixture, day: date = MONDAY, **overrides: Any
) -> dict[str, Any]:
    params: dict[str, Any] = {"date": day.isoformat(), "service_id": str(fx.service.id)}
    params.update(overrides)
    response = await client.get(url(fx.barber.id), params=params)
    return {"status": response.status_code, "body": response.json()}


class TestHappyPath:
    async def test_a_full_open_day(
        self, client: AsyncClient, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        fx = await booked()

        result = await fetch(client, fx)

        assert result["status"] == 200
        body = result["body"]
        assert body["timezone"] == "Asia/Almaty"
        assert body["service"]["duration_minutes"] == 45
        # 09:00-18:00 on a 15-minute grid: starts run 09:00 .. 17:15, because
        # 17:15 is the last one whose 45 minutes still end by closing time.
        assert len(body["slots"]) == 34
        assert body["slots"][0]["start_local"] == "09:00"
        assert body["slots"][-1]["start_local"] == "17:15"

    async def test_utc_instants_match_the_local_rendering(
        self, client: AsyncClient, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        """Almaty is UTC+5, so 09:00 local is 04:00Z."""
        fx = await booked()

        first = (await fetch(client, fx))["body"]["slots"][0]

        assert first["start"] == "2027-03-15T04:00:00Z"
        assert first["start_local"] == "09:00"
        assert first["end_local"] == "09:45"

    async def test_slots_sit_on_the_local_quarter_hour_grid(
        self, client: AsyncClient, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        fx = await booked()

        slots = (await fetch(client, fx))["body"]["slots"]

        assert all(s["start_local"].split(":")[1] in {"00", "15", "30", "45"} for s in slots)

    async def test_a_day_with_no_shift_returns_nothing(
        self, client: AsyncClient, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        fx = await booked()

        result = await fetch(client, fx, day=MONDAY + timedelta(days=1))

        assert result["status"] == 200
        assert result["body"]["slots"] == []

    async def test_split_shifts_produce_two_runs_of_slots(
        self, client: AsyncClient, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        fx = await booked(shifts=[(time(9), time(13)), (time(14), time(19))])

        locals_ = [s["start_local"] for s in (await fetch(client, fx))["body"]["slots"]]

        assert "12:15" in locals_
        assert "13:00" not in locals_  # inside the lunch gap
        assert "14:00" in locals_

    async def test_the_endpoint_is_public(
        self, client: AsyncClient, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        fx = await booked()

        assert (await fetch(client, fx))["status"] == 200


class TestBlockedTime:
    async def test_an_appointment_removes_its_slots(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        booked: Callable[..., Awaitable[Fixture]],
    ) -> None:
        fx = await booked()
        start = datetime(2027, 3, 15, 5, tzinfo=UTC)  # 10:00 Almaty
        db.add(
            Appointment(
                customer_id=(await make_user()).id,
                barber_id=fx.barber.id,
                service_id=fx.service.id,
                start_time=start,
                end_time=start + timedelta(minutes=45),
                total_price=Decimal("5000"),
            )
        )
        await db.flush()

        locals_ = [s["start_local"] for s in (await fetch(client, fx))["body"]["slots"]]

        assert "09:00" in locals_
        assert "10:00" not in locals_
        assert "10:30" not in locals_  # would run into the booking
        assert "10:45" in locals_

    async def test_a_cancelled_appointment_frees_its_slots(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        booked: Callable[..., Awaitable[Fixture]],
    ) -> None:
        fx = await booked()
        start = datetime(2027, 3, 15, 5, tzinfo=UTC)
        db.add(
            Appointment(
                customer_id=(await make_user()).id,
                barber_id=fx.barber.id,
                service_id=fx.service.id,
                start_time=start,
                end_time=start + timedelta(minutes=45),
                total_price=Decimal("5000"),
                status=AppointmentStatus.CANCELLED,
            )
        )
        await db.flush()

        locals_ = [s["start_local"] for s in (await fetch(client, fx))["body"]["slots"]]

        assert "10:00" in locals_

    async def test_a_break_removes_its_slots(
        self, client: AsyncClient, db: AsyncSession, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        fx = await booked()
        db.add(
            BarberBreak(
                barber_id=fx.barber.id,
                start_datetime=datetime(2027, 3, 15, 8, tzinfo=UTC),  # 13:00 Almaty
                end_datetime=datetime(2027, 3, 15, 9, tzinfo=UTC),
                reason="Lunch",
            )
        )
        await db.flush()

        locals_ = [s["start_local"] for s in (await fetch(client, fx))["body"]["slots"]]

        assert "12:15" in locals_
        assert "13:00" not in locals_
        assert "14:00" in locals_

    async def test_a_whole_day_break_clears_the_day(
        self, client: AsyncClient, db: AsyncSession, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        """This is how a day off is expressed — there is no separate table (§11.12)."""
        fx = await booked()
        db.add(
            BarberBreak(
                barber_id=fx.barber.id,
                start_datetime=datetime(2027, 3, 14, 19, tzinfo=UTC),
                end_datetime=datetime(2027, 3, 15, 19, tzinfo=UTC),
                reason="Day off",
            )
        )
        await db.flush()

        assert (await fetch(client, fx))["body"]["slots"] == []

    async def test_another_barbers_appointment_is_ignored(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_barber: BarberFactory,
        booked: Callable[..., Awaitable[Fixture]],
    ) -> None:
        fx = await booked()
        colleague = await make_barber(await make_user(role=UserRole.BARBER), fx.shop)
        start = datetime(2027, 3, 15, 5, tzinfo=UTC)
        db.add(
            Appointment(
                customer_id=(await make_user()).id,
                barber_id=colleague.id,
                service_id=fx.service.id,
                start_time=start,
                end_time=start + timedelta(minutes=45),
                total_price=Decimal("5000"),
            )
        )
        await db.flush()

        locals_ = [s["start_local"] for s in (await fetch(client, fx))["body"]["slots"]]

        assert "10:00" in locals_


class TestServiceRules:
    async def test_service_id_is_required(
        self, client: AsyncClient, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        """Slot width is the service duration, so the date alone is not enough (§11.6)."""
        fx = await booked()

        response = await client.get(url(fx.barber.id), params={"date": MONDAY.isoformat()})

        assert response.status_code == 422

    async def test_date_is_required(
        self, client: AsyncClient, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        fx = await booked()

        response = await client.get(url(fx.barber.id), params={"service_id": str(fx.service.id)})

        assert response.status_code == 422

    async def test_a_longer_service_yields_fewer_slots(
        self, client: AsyncClient, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        short = await booked(duration_minutes=30)
        long = await booked(duration_minutes=120)

        short_count = len((await fetch(client, short))["body"]["slots"])
        long_count = len((await fetch(client, long))["body"]["slots"])

        assert short_count > long_count

    async def test_a_service_the_barber_does_not_perform_is_rejected(
        self,
        client: AsyncClient,
        db: AsyncSession,
        booked: Callable[..., Awaitable[Fixture]],
    ) -> None:
        fx = await booked()
        unassigned = Service(
            shop_id=fx.shop.id, name="Beard", duration_minutes=30, price=Decimal("3000")
        )
        db.add(unassigned)
        await db.flush()

        response = await client.get(
            url(fx.barber.id),
            params={"date": MONDAY.isoformat(), "service_id": str(unassigned.id)},
        )

        assert response.status_code == 422
        assert "does not perform" in response.json()["error"]["message"]

    async def test_a_service_from_another_shop_is_rejected(
        self,
        client: AsyncClient,
        booked: Callable[..., Awaitable[Fixture]],
    ) -> None:
        mine = await booked()
        theirs = await booked()

        response = await client.get(
            url(mine.barber.id),
            params={"date": MONDAY.isoformat(), "service_id": str(theirs.service.id)},
        )

        assert response.status_code == 422

    async def test_a_retired_service_is_not_found(
        self, client: AsyncClient, db: AsyncSession, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        fx = await booked()
        fx.service.is_active = False
        await db.flush()

        assert (await fetch(client, fx))["status"] == 404

    async def test_unknown_barber_is_404(
        self, client: AsyncClient, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        fx = await booked()

        response = await client.get(
            url(uuid.uuid4()),
            params={"date": MONDAY.isoformat(), "service_id": str(fx.service.id)},
        )

        assert response.status_code == 404

    async def test_a_removed_barber_is_404(
        self, client: AsyncClient, db: AsyncSession, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        fx = await booked()
        fx.barber.is_active = False
        await db.flush()

        assert (await fetch(client, fx))["status"] == 404


class TestLeadTime:
    async def test_slots_too_soon_are_hidden(
        self,
        client: AsyncClient,
        db: AsyncSession,
        booked: Callable[..., Awaitable[Fixture]],
    ) -> None:
        """Today's already-passed hours must not be offered."""
        fx = await booked(weekday=date.today().weekday())

        body = (await fetch(client, fx, day=date.today()))["body"]

        now_local = datetime.now(UTC)
        for slot in body["slots"]:
            assert datetime.fromisoformat(slot["start"]) > now_local


class TestTimezones:
    async def test_the_same_shift_lands_at_different_utc_instants(
        self, client: AsyncClient, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        almaty = await booked(timezone="Asia/Almaty")
        lisbon = await booked(timezone="Europe/Lisbon")

        first_almaty = (await fetch(client, almaty))["body"]["slots"][0]
        first_lisbon = (await fetch(client, lisbon))["body"]["slots"][0]

        assert first_almaty["start_local"] == first_lisbon["start_local"] == "09:00"
        assert first_almaty["start"] != first_lisbon["start"]

    async def test_a_dst_spring_forward_day_has_fewer_slots(
        self, client: AsyncClient, booked: Callable[..., Awaitable[Fixture]]
    ) -> None:
        """Berlin loses an hour on 2027-03-28; a 00:00-08:00 shift is 7 real hours."""
        spring = date(2027, 3, 28)
        normal = date(2027, 3, 21)
        fx = await booked(
            timezone="Europe/Berlin",
            duration_minutes=60,
            shifts=[(time(0), time(8))],
            weekday=spring.weekday(),
        )

        on_normal = len((await fetch(client, fx, day=normal))["body"]["slots"])
        on_spring = len((await fetch(client, fx, day=spring))["body"]["slots"])

        # 8 real hours, 60-minute service, 15-minute grid: starts 00:00 .. 07:00.
        assert on_normal == 29
        # One real hour vanishes, so four 15-minute starts go with it.
        assert on_spring == 25
