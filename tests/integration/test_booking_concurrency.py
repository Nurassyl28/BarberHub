"""The double-booking guarantee, under a real race (docs/SPEC.md §5, §10).

Unlike the rest of the suite, these tests commit for real on independent
connections — a shared session would serialize the very contention being
tested, and the exclusion constraint would never be exercised. Everything
created here is torn down explicitly.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from app.core.errors import SlotTakenError
from app.db.session import SessionFactory
from app.models import (
    Appointment,
    AppointmentStatus,
    Barber,
    Barbershop,
    Review,
    Service,
    User,
    UserRole,
    WorkingHours,
    barber_services,
)
from app.schemas.appointment import AppointmentCreate
from app.schemas.review import ReviewCreate
from app.services import booking, reviews
from tests.conftest import unique_email

CONTENDERS = 10


class Stage:
    """Committed rows for one race, plus the ids needed to clean them up."""

    def __init__(self) -> None:
        self.shop_id: uuid.UUID
        self.barber_id: uuid.UUID
        self.service_id: uuid.UUID
        self.user_ids: list[uuid.UUID] = []
        self.start: datetime


@pytest.fixture
async def stage() -> AsyncGenerator[Stage]:
    st = Stage()
    async with SessionFactory() as session:
        owner = User(
            first_name="Race",
            last_name="Owner",
            email=unique_email("race-owner"),
            password_hash="x",
            role=UserRole.SHOP_OWNER,
        )
        barber_user = User(
            first_name="Race",
            last_name="Barber",
            email=unique_email("race-barber"),
            password_hash="x",
            role=UserRole.BARBER,
        )
        customers = [
            User(
                first_name=f"C{i}",
                last_name="Racer",
                email=unique_email(f"race-cust{i}"),
                password_hash="x",
            )
            for i in range(CONTENDERS)
        ]
        session.add_all([owner, barber_user, *customers])
        await session.flush()

        shop = Barbershop(
            owner_id=owner.id,
            name="Race Cuts",
            address="Abay 1",
            city="Almaty",
            timezone="Asia/Almaty",
        )
        session.add(shop)
        await session.flush()

        barber = Barber(user_id=barber_user.id, shop_id=shop.id)
        service = Service(
            shop_id=shop.id, name="Haircut", duration_minutes=45, price=Decimal("5000")
        )
        session.add_all([barber, service])
        await session.flush()
        await session.execute(
            barber_services.insert().values(barber_id=barber.id, service_id=service.id)
        )
        for weekday in range(7):
            session.add(
                WorkingHours(
                    barber_id=barber.id,
                    weekday=weekday,
                    start_time=time(0),
                    end_time=time(23, 59),
                )
            )
        await session.commit()

        st.shop_id = shop.id
        st.barber_id = barber.id
        st.service_id = service.id
        st.user_ids = [owner.id, barber_user.id, *[c.id for c in customers]]
        # A round hour well in the future: on the grid, past the booking lead.
        st.start = (datetime.now(UTC) + timedelta(days=30)).replace(
            minute=0, second=0, microsecond=0
        )

    yield st

    async with SessionFactory() as session:
        await session.execute(delete(Review).where(Review.barber_id == st.barber_id))
        await session.execute(delete(Appointment).where(Appointment.barber_id == st.barber_id))
        await session.execute(
            barber_services.delete().where(barber_services.c.barber_id == st.barber_id)
        )
        await session.execute(delete(WorkingHours).where(WorkingHours.barber_id == st.barber_id))
        await session.execute(delete(Service).where(Service.id == st.service_id))
        await session.execute(delete(Barber).where(Barber.id == st.barber_id))
        await session.execute(delete(Barbershop).where(Barbershop.id == st.shop_id))
        await session.execute(delete(User).where(User.id.in_(st.user_ids)))
        await session.commit()


async def attempt(customer_id: uuid.UUID, st: Stage, start: datetime) -> str:
    """One booking attempt on its own connection."""
    async with SessionFactory() as session:
        customer = await session.get(User, customer_id)
        assert customer is not None
        try:
            await booking.create_appointment(
                session,
                customer,
                AppointmentCreate(
                    barber_id=st.barber_id, service_id=st.service_id, start_time=start
                ),
            )
        except SlotTakenError:
            return "SLOT_TAKEN"
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
        return "BOOKED"


async def count_active(st: Stage, start: datetime) -> int:
    async with SessionFactory() as session:
        result = await session.execute(
            select(func.count())
            .select_from(Appointment)
            .where(Appointment.barber_id == st.barber_id, Appointment.start_time == start)
        )
        return result.scalar_one()


async def test_ten_customers_racing_for_one_slot_yield_exactly_one_booking(
    stage: Stage,
) -> None:
    outcomes = await asyncio.gather(
        *(attempt(cid, stage, stage.start) for cid in stage.user_ids[2:])
    )

    assert outcomes.count("BOOKED") == 1, outcomes
    assert outcomes.count("SLOT_TAKEN") == CONTENDERS - 1, outcomes
    assert await count_active(stage, stage.start) == 1


async def test_racing_customers_never_produce_an_unexpected_error(stage: Stage) -> None:
    """Every loser must get the clean 409, never a raw IntegrityError or a 500."""
    outcomes = await asyncio.gather(
        *(attempt(cid, stage, stage.start) for cid in stage.user_ids[2:])
    )

    assert set(outcomes) <= {"BOOKED", "SLOT_TAKEN"}, outcomes


async def test_overlapping_starts_also_collide(stage: Stage) -> None:
    """Different start times, one 45-minute service: only one can win."""
    offsets = [timedelta(0), timedelta(minutes=15), timedelta(minutes=30)]

    outcomes = await asyncio.gather(
        *(
            attempt(cid, stage, stage.start + offset)
            for cid, offset in zip(stage.user_ids[2:5], offsets, strict=True)
        )
    )

    assert outcomes.count("BOOKED") == 1, outcomes


async def test_non_overlapping_starts_all_succeed(stage: Stage) -> None:
    """The constraint must not serialize bookings that do not actually clash."""
    offsets = [timedelta(0), timedelta(minutes=45), timedelta(minutes=90)]

    outcomes = await asyncio.gather(
        *(
            attempt(cid, stage, stage.start + offset)
            for cid, offset in zip(stage.user_ids[2:5], offsets, strict=True)
        )
    )

    assert outcomes == ["BOOKED", "BOOKED", "BOOKED"], outcomes


# --------------------------------------------------------------------------- #
# rating recomputation under contention
# --------------------------------------------------------------------------- #


async def _completed(st: Stage, customer_id: uuid.UUID, offset: int) -> uuid.UUID:
    """Commit a finished appointment so it can be reviewed from another session."""
    async with SessionFactory() as session:
        start = datetime.now(UTC) - timedelta(days=1, hours=offset)
        appointment = Appointment(
            customer_id=customer_id,
            barber_id=st.barber_id,
            service_id=st.service_id,
            start_time=start,
            end_time=start + timedelta(minutes=45),
            total_price=Decimal("5000"),
            status=AppointmentStatus.COMPLETED,
        )
        session.add(appointment)
        await session.commit()
        return appointment.id


async def _review(appointment_id: uuid.UUID, customer_id: uuid.UUID, rating: int) -> str:
    async with SessionFactory() as session:
        customer = await session.get(User, customer_id)
        assert customer is not None
        try:
            await reviews.create_review(
                session, customer, appointment_id, ReviewCreate(rating=rating)
            )
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
        return "OK"


async def test_concurrent_reviews_keep_the_rating_consistent(stage: Stage) -> None:
    """Five reviews landing at once must not lose a count.

    Recomputing a denormalized average is a read-modify-write, so without the
    row lock in `recompute_barber_rating` two writers would both read the old
    set and store a count one short.
    """
    customers = stage.user_ids[2:7]
    appointments = [await _completed(stage, cid, offset) for offset, cid in enumerate(customers)]
    scores = [5, 4, 3, 5, 3]

    outcomes = await asyncio.gather(
        *(
            _review(appointment_id, customer_id, score)
            for appointment_id, customer_id, score in zip(
                appointments, customers, scores, strict=True
            )
        )
    )

    assert set(outcomes) == {"OK"}, outcomes

    async with SessionFactory() as session:
        barber = await session.get(Barber, stage.barber_id)
        assert barber is not None
        average, count = (
            await session.execute(
                select(func.avg(Review.rating), func.count()).where(
                    Review.barber_id == stage.barber_id
                )
            )
        ).one()

        assert count == len(scores)
        assert barber.reviews_count == count
        assert barber.rating == Decimal(average).quantize(Decimal("0.01"))
