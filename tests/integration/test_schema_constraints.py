"""The schema guarantees from docs/SPEC.md §3 — asserted against real Postgres."""

import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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
)
from tests.conftest import unique_email

BASE = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)


async def _count_for_barber(db: AsyncSession, barber_id: uuid.UUID) -> int:
    """Scoped to one barber: the database is shared, so a global count is not ours."""
    result = await db.execute(
        select(func.count()).select_from(Appointment).where(Appointment.barber_id == barber_id)
    )
    return result.scalar_one()


def _appointment(fx: dict[str, Any], offset: int, minutes: int = 45, **kw: Any) -> Appointment:
    start = BASE + timedelta(minutes=offset)
    return Appointment(
        customer_id=fx["customer"].id,
        barber_id=fx["barber"].id,
        service_id=fx["service"].id,
        start_time=start,
        end_time=start + timedelta(minutes=minutes),
        total_price=Decimal("5000.00"),
        **kw,
    )


class TestDoubleBooking:
    """The exclusion constraint is the authority on availability (§5)."""

    async def test_overlapping_booking_is_rejected(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        db.add(_appointment(shop_fixture, 0))
        await db.flush()

        db.add(_appointment(shop_fixture, 30))  # 03:30–04:15 overlaps 03:00–03:45
        with pytest.raises(IntegrityError) as excinfo:
            await db.flush()

        assert excinfo.value.orig.sqlstate == "23P01"  # type: ignore[union-attr]

    async def test_adjacent_booking_is_allowed(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        """`[)` bounds: one appointment may start exactly when another ends."""
        db.add(_appointment(shop_fixture, 0))
        await db.flush()

        db.add(_appointment(shop_fixture, 45))  # 03:45–04:30
        await db.flush()

        assert await _count_for_barber(db, shop_fixture["barber"].id) == 2

    async def test_cancelled_appointment_frees_the_slot(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        """The constraint is filtered on active statuses, so cancelling releases the time."""
        first = _appointment(shop_fixture, 0, status=AppointmentStatus.CANCELLED)
        db.add(first)
        await db.flush()

        db.add(_appointment(shop_fixture, 0))
        await db.flush()

        assert await _count_for_barber(db, shop_fixture["barber"].id) == 2

    async def test_other_barbers_are_unaffected(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        other_user = User(
            first_name="Nurlan",
            last_name="Second",
            email=unique_email("barber2"),
            password_hash="x",
            role=UserRole.BARBER,
        )
        db.add(other_user)
        await db.flush()
        other_barber = Barber(user_id=other_user.id, shop_id=shop_fixture["shop"].id)
        db.add(other_barber)
        await db.flush()

        db.add(_appointment(shop_fixture, 0))
        await db.flush()

        same_time = _appointment(shop_fixture, 0)
        same_time.barber_id = other_barber.id
        db.add(same_time)
        await db.flush()

        assert await _count_for_barber(db, shop_fixture["barber"].id) == 1
        assert await _count_for_barber(db, other_barber.id) == 1


class TestCheckConstraints:
    async def test_appointment_must_end_after_it_starts(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        db.add(_appointment(shop_fixture, 0, minutes=-30))
        with pytest.raises(IntegrityError):
            await db.flush()

    async def test_service_duration_must_be_positive(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        db.add(
            Service(
                shop_id=shop_fixture["shop"].id,
                name="Broken",
                duration_minutes=0,
                price=Decimal("100"),
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()

    async def test_working_hours_weekday_is_bounded(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        db.add(
            WorkingHours(
                barber_id=shop_fixture["barber"].id,
                weekday=7,
                start_time=time(9, 0),
                end_time=time(18, 0),
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()

    async def test_review_rating_is_bounded(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        appointment = _appointment(shop_fixture, 0, status=AppointmentStatus.COMPLETED)
        db.add(appointment)
        await db.flush()

        db.add(
            Review(
                customer_id=shop_fixture["customer"].id,
                barber_id=shop_fixture["barber"].id,
                appointment_id=appointment.id,
                rating=6,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()


class TestUniqueness:
    async def test_email_is_case_insensitive(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        taken = shop_fixture["customer"].email
        db.add(
            User(
                first_name="Impostor",
                last_name="Same",
                email=taken.upper(),
                password_hash="x",
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()

    async def test_service_name_is_unique_per_shop_case_insensitively(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        db.add(
            Service(
                shop_id=shop_fixture["shop"].id,
                name="haircut",  # existing fixture has "Haircut"
                duration_minutes=30,
                price=Decimal("100"),
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()

    async def test_one_review_per_appointment(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        appointment = _appointment(shop_fixture, 0, status=AppointmentStatus.COMPLETED)
        db.add(appointment)
        await db.flush()

        for _ in range(2):
            db.add(
                Review(
                    customer_id=shop_fixture["customer"].id,
                    barber_id=shop_fixture["barber"].id,
                    appointment_id=appointment.id,
                    rating=5,
                )
            )
        with pytest.raises(IntegrityError):
            await db.flush()

    async def test_barber_cannot_be_added_to_the_same_shop_twice(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        db.add(
            Barber(
                user_id=shop_fixture["barber_user"].id,
                shop_id=shop_fixture["shop"].id,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()


class TestDefaults:
    async def test_timestamps_are_generated_per_row(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        """Regression: `server_default="now()"` as a plain string freezes a constant."""
        review_target = _appointment(shop_fixture, 0, status=AppointmentStatus.COMPLETED)
        db.add(review_target)
        await db.flush()

        shop = Barbershop(
            owner_id=shop_fixture["owner"].id, name="Second", address="b", city="Astana"
        )
        db.add(shop)
        await db.flush()
        await db.refresh(shop)
        await db.refresh(review_target)

        # Compare against the database's own wall clock, not Python's: `now()` is
        # the *transaction* timestamp, so a host clock jump (a laptop sleeping
        # mid-run) would otherwise fail this for no real reason.
        db_now = (await db.execute(select(func.clock_timestamp()))).scalar_one()
        for row in (shop, review_target):
            assert abs((db_now - row.created_at).total_seconds()) < 60

    async def test_new_user_defaults_to_customer(self, db: AsyncSession) -> None:
        user = User(first_name="A", last_name="B", email=unique_email(), password_hash="x")
        db.add(user)
        await db.flush()
        await db.refresh(user)

        assert user.role is UserRole.CUSTOMER
        assert user.is_active is True

    async def test_new_appointment_defaults_to_confirmed(
        self, db: AsyncSession, shop_fixture: dict[str, Any]
    ) -> None:
        appointment = _appointment(shop_fixture, 0)
        db.add(appointment)
        await db.flush()
        await db.refresh(appointment)

        assert appointment.status is AppointmentStatus.CONFIRMED
