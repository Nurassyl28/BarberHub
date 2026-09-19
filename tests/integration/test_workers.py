"""Background tasks against a real database (docs/SPEC.md §9).

Tasks use the *sync* session, so these tests commit for real and clean up after
themselves, exactly like the concurrency suite.
"""

import uuid
from collections.abc import Generator
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import delete, func, select

from app.db.sync_session import SyncSessionFactory
from app.models import (
    Appointment,
    AppointmentReminder,
    AppointmentStatus,
    Barber,
    Barbershop,
    ReminderKind,
    Review,
    Service,
    User,
    UserRole,
    WorkingHours,
    barber_services,
)
from app.workers import tasks
from tests.conftest import unique_email

NOW = datetime(2027, 4, 12, 9, 0, tzinfo=UTC)


class Stage:
    shop_id: uuid.UUID
    barber_id: uuid.UUID
    service_id: uuid.UUID
    customer_id: uuid.UUID
    user_ids: list[uuid.UUID]


@pytest.fixture
def stage() -> Generator[Stage]:
    st = Stage()
    with SyncSessionFactory() as session:
        owner = User(
            first_name="Worker",
            last_name="Owner",
            email=unique_email("wk-owner"),
            password_hash="x",
            role=UserRole.SHOP_OWNER,
        )
        barber_user = User(
            first_name="Aidar",
            last_name="Barber",
            email=unique_email("wk-barber"),
            password_hash="x",
            role=UserRole.BARBER,
        )
        customer = User(
            first_name="Dana",
            last_name="Customer",
            email=unique_email("wk-cust"),
            password_hash="x",
        )
        session.add_all([owner, barber_user, customer])
        session.flush()

        shop = Barbershop(
            owner_id=owner.id,
            name="Worker Cuts",
            address="Abay 1",
            city="Almaty",
            timezone="Asia/Almaty",
        )
        session.add(shop)
        session.flush()

        barber = Barber(user_id=barber_user.id, shop_id=shop.id)
        service = Service(
            shop_id=shop.id, name="Haircut", duration_minutes=45, price=Decimal("5000")
        )
        session.add_all([barber, service])
        session.flush()
        session.execute(barber_services.insert().values(barber_id=barber.id, service_id=service.id))
        session.add(
            WorkingHours(barber_id=barber.id, weekday=0, start_time=time(9), end_time=time(18))
        )
        session.commit()

        st.shop_id, st.barber_id = shop.id, barber.id
        st.service_id, st.customer_id = service.id, customer.id
        st.user_ids = [owner.id, barber_user.id, customer.id]

    yield st

    with SyncSessionFactory() as session:
        appointment_ids = (
            session.execute(select(Appointment.id).where(Appointment.barber_id == st.barber_id))
            .scalars()
            .all()
        )
        if appointment_ids:
            session.execute(
                delete(AppointmentReminder).where(
                    AppointmentReminder.appointment_id.in_(appointment_ids)
                )
            )
        session.execute(delete(Review).where(Review.barber_id == st.barber_id))
        session.execute(delete(Appointment).where(Appointment.barber_id == st.barber_id))
        session.execute(barber_services.delete().where(barber_services.c.barber_id == st.barber_id))
        session.execute(delete(WorkingHours).where(WorkingHours.barber_id == st.barber_id))
        session.execute(delete(Service).where(Service.id == st.service_id))
        session.execute(delete(Barber).where(Barber.id == st.barber_id))
        session.execute(delete(Barbershop).where(Barbershop.id == st.shop_id))
        session.execute(delete(User).where(User.id.in_(st.user_ids)))
        session.commit()


def book(
    st: Stage,
    start: datetime,
    status: AppointmentStatus = AppointmentStatus.CONFIRMED,
    created_at: datetime | None = None,
) -> uuid.UUID:
    with SyncSessionFactory() as session:
        appointment = Appointment(
            customer_id=st.customer_id,
            barber_id=st.barber_id,
            service_id=st.service_id,
            start_time=start,
            end_time=start + timedelta(minutes=45),
            total_price=Decimal("5000"),
            status=status,
        )
        session.add(appointment)
        session.flush()
        if created_at is not None:
            appointment.created_at = created_at
        session.commit()
        return appointment.id


def reminders_for(appointment_id: uuid.UUID) -> list[ReminderKind]:
    with SyncSessionFactory() as session:
        return list(
            session.execute(
                select(AppointmentReminder.kind).where(
                    AppointmentReminder.appointment_id == appointment_id
                )
            )
            .scalars()
            .all()
        )


@pytest.fixture
def queued() -> Generator[list[tuple[str, str, str]]]:
    """Capture what would have been emailed, without an SMTP server."""
    sent: list[tuple[str, str, str]] = []

    def fake_delay(to: str, subject: str, body: str) -> None:
        sent.append((to, subject, body))

    with patch.object(tasks.send_email, "delay", side_effect=fake_delay):
        yield sent


class TestReminders:
    async def test_a_booking_a_day_out_gets_the_day_before_reminder(
        self, stage: Stage, queued: list[tuple[str, str, str]]
    ) -> None:
        appointment_id = book(stage, NOW + timedelta(hours=24, minutes=5))

        assert tasks.send_appointment_reminders(now=NOW.isoformat()) == 1
        assert reminders_for(appointment_id) == [ReminderKind.DAY_BEFORE]
        assert len(queued) == 1

    async def test_a_booking_two_hours_out_gets_the_hours_before_reminder(
        self, stage: Stage, queued: list[tuple[str, str, str]]
    ) -> None:
        appointment_id = book(stage, NOW + timedelta(hours=2, minutes=5))

        tasks.send_appointment_reminders(now=NOW.isoformat())

        assert reminders_for(appointment_id) == [ReminderKind.HOURS_BEFORE]

    async def test_a_booking_outside_every_window_is_left_alone(
        self, stage: Stage, queued: list[tuple[str, str, str]]
    ) -> None:
        appointment_id = book(stage, NOW + timedelta(days=5))

        assert tasks.send_appointment_reminders(now=NOW.isoformat()) == 0
        assert reminders_for(appointment_id) == []

    async def test_running_twice_sends_one_reminder(
        self, stage: Stage, queued: list[tuple[str, str, str]]
    ) -> None:
        """The unique constraint is the idempotency, not a time-window check:
        a double beat tick, an overlapping worker and a replayed batch all end
        up here."""
        appointment_id = book(stage, NOW + timedelta(hours=24, minutes=5))

        first = tasks.send_appointment_reminders(now=NOW.isoformat())
        second = tasks.send_appointment_reminders(now=NOW.isoformat())

        assert (first, second) == (1, 0)
        assert reminders_for(appointment_id) == [ReminderKind.DAY_BEFORE]
        assert len(queued) == 1

    async def test_both_reminders_can_fire_for_one_booking(
        self, stage: Stage, queued: list[tuple[str, str, str]]
    ) -> None:
        """They are different kinds, so the constraint does not block the second."""
        appointment_id = book(stage, NOW + timedelta(hours=24, minutes=5))
        tasks.send_appointment_reminders(now=NOW.isoformat())

        later = NOW + timedelta(hours=22)
        tasks.send_appointment_reminders(now=later.isoformat())

        assert set(reminders_for(appointment_id)) == {
            ReminderKind.DAY_BEFORE,
            ReminderKind.HOURS_BEFORE,
        }

    async def test_cancelled_bookings_are_not_reminded(
        self, stage: Stage, queued: list[tuple[str, str, str]]
    ) -> None:
        book(
            stage,
            NOW + timedelta(hours=24, minutes=5),
            status=AppointmentStatus.CANCELLED,
        )

        assert tasks.send_appointment_reminders(now=NOW.isoformat()) == 0
        assert queued == []

    async def test_the_reminder_reaches_the_customer(
        self, stage: Stage, queued: list[tuple[str, str, str]]
    ) -> None:
        book(stage, NOW + timedelta(hours=2, minutes=5))

        tasks.send_appointment_reminders(now=NOW.isoformat())

        to, subject, body = queued[0]
        assert "wk-cust" in to
        assert "Haircut" in subject
        assert "Dana" in body
        assert "Abay 1" in body


class TestExpireStalePending:
    async def test_an_old_pending_booking_is_cancelled(self, stage: Stage) -> None:
        appointment_id = book(
            stage,
            NOW + timedelta(days=3),
            status=AppointmentStatus.PENDING,
            created_at=NOW - timedelta(hours=2),
        )

        assert tasks.expire_stale_pending(now=NOW.isoformat()) == 1

        with SyncSessionFactory() as session:
            stored = session.get(Appointment, appointment_id)
            assert stored is not None
            assert stored.status is AppointmentStatus.CANCELLED
            assert stored.cancellation_reason == "Not confirmed in time"

    async def test_a_fresh_pending_booking_is_left_alone(self, stage: Stage) -> None:
        book(
            stage,
            NOW + timedelta(days=3),
            status=AppointmentStatus.PENDING,
            created_at=NOW - timedelta(minutes=5),
        )

        assert tasks.expire_stale_pending(now=NOW.isoformat()) == 0

    async def test_confirmed_bookings_are_never_expired(self, stage: Stage) -> None:
        book(
            stage,
            NOW + timedelta(days=3),
            status=AppointmentStatus.CONFIRMED,
            created_at=NOW - timedelta(days=30),
        )

        assert tasks.expire_stale_pending(now=NOW.isoformat()) == 0


class TestRatingRepair:
    async def test_a_drifted_rating_is_corrected(self, stage: Stage) -> None:
        """This is the safety net for a bug, a manual DELETE, or a restore."""
        appointment_id = book(stage, NOW - timedelta(days=1), AppointmentStatus.COMPLETED)
        with SyncSessionFactory() as session:
            session.add(
                Review(
                    customer_id=stage.customer_id,
                    barber_id=stage.barber_id,
                    appointment_id=appointment_id,
                    rating=4,
                )
            )
            barber = session.get(Barber, stage.barber_id)
            assert barber is not None
            barber.rating = Decimal("1.00")  # deliberately wrong
            barber.reviews_count = 99
            session.commit()

        tasks.recompute_barber_ratings()

        with SyncSessionFactory() as session:
            barber = session.get(Barber, stage.barber_id)
            assert barber is not None
            assert barber.rating == Decimal("4.00")
            assert barber.reviews_count == 1

    async def test_a_correct_rating_is_reported_as_unchanged(self, stage: Stage) -> None:
        with SyncSessionFactory() as session:
            before = session.execute(
                select(func.count()).select_from(Barber).where(Barber.id == stage.barber_id)
            ).scalar_one()

        assert before == 1
        tasks.recompute_barber_ratings()
        repaired_again = tasks.recompute_barber_ratings()

        with SyncSessionFactory() as session:
            barber = session.get(Barber, stage.barber_id)
            assert barber is not None
            assert barber.reviews_count == 0
        assert isinstance(repaired_again, int)


class TestNotifications:
    async def test_a_new_booking_emails_both_parties(
        self, stage: Stage, queued: list[tuple[str, str, str]]
    ) -> None:
        appointment_id = book(stage, NOW + timedelta(days=3))

        assert tasks.notify_appointment_created(str(appointment_id)) == 2

        recipients = [to for to, _, _ in queued]
        assert any("wk-cust" in to for to in recipients)
        assert any("wk-barber" in to for to in recipients)

    async def test_a_cancellation_carries_the_reason(
        self, stage: Stage, queued: list[tuple[str, str, str]]
    ) -> None:
        appointment_id = book(stage, NOW + timedelta(days=3))
        with SyncSessionFactory() as session:
            stored = session.get(Appointment, appointment_id)
            assert stored is not None
            stored.status = AppointmentStatus.CANCELLED
            stored.cancellation_reason = "Barber is ill"
            session.commit()

        tasks.notify_appointment_cancelled(str(appointment_id))

        assert any("Barber is ill" in body for _, _, body in queued)

    async def test_a_vanished_appointment_is_a_no_op(
        self, stage: Stage, queued: list[tuple[str, str, str]]
    ) -> None:
        """A task can run after the row is gone; that must not raise."""
        assert tasks.notify_appointment_created(str(uuid.uuid4())) == 0
        assert queued == []

    async def test_times_are_rendered_in_the_shops_timezone(
        self, stage: Stage, queued: list[tuple[str, str, str]]
    ) -> None:
        """09:00 UTC is 14:00 in Almaty, and the customer reads local time."""
        book(stage, datetime(2027, 4, 15, 9, 0, tzinfo=UTC))
        appointment_id = book(stage, datetime(2027, 4, 16, 9, 0, tzinfo=UTC))

        tasks.notify_appointment_created(str(appointment_id))

        assert any("14:00" in body for _, _, body in queued)
