"""Background tasks (docs/SPEC.md §9).

These run in Celery workers, which are not async, so everything here uses the
sync session from `app.db.sync_session` against the same models the API uses.
"""

import logging
import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from celery import shared_task
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.db.sync_session import sync_session
from app.models import (
    Appointment,
    AppointmentReminder,
    AppointmentStatus,
    Barber,
    ReminderKind,
    Review,
)
from app.services.slots import shop_timezone
from app.utils.time import utcnow
from app.workers.email import Email, render, send

logger = logging.getLogger(__name__)

#: How far ahead each reminder looks, and the window it scans.
REMINDER_HORIZONS: dict[ReminderKind, timedelta] = {
    ReminderKind.DAY_BEFORE: timedelta(hours=24),
    ReminderKind.HOURS_BEFORE: timedelta(hours=2),
}

#: Must be at least the beat interval, or appointments fall between two sweeps.
REMINDER_WINDOW = timedelta(minutes=20)

_ACTIVE = (AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED)


@shared_task(
    name="app.workers.tasks.send_email",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
)
def send_email(self: Any, to: str, subject: str, body: str) -> str:
    """Deliver one message, retrying a few times on transient SMTP failure."""
    try:
        send(Email(to=to, subject=subject, body=body))
    except Exception as exc:
        logger.warning("email to %s failed: %s", to, exc)
        raise self.retry(exc=exc) from exc
    return to


@shared_task(name="app.workers.tasks.notify_appointment_created")
def notify_appointment_created(appointment_id: str) -> int:
    """Confirmation to the customer, a heads-up to the barber."""
    with sync_session() as session:
        appointment = _load(session, uuid.UUID(appointment_id))
        if appointment is None:
            return 0
        context = _context(appointment)

        subject, body = render("appointment_created", **context)
        send_email.delay(appointment.customer.email, subject, body)

        subject, body = render("barber_appointment_created", **context)
        send_email.delay(appointment.barber.user.email, subject, body)
        return 2


@shared_task(name="app.workers.tasks.notify_appointment_cancelled")
def notify_appointment_cancelled(appointment_id: str) -> int:
    with sync_session() as session:
        appointment = _load(session, uuid.UUID(appointment_id))
        if appointment is None:
            return 0
        context = _context(appointment)
        context["reason"] = (
            f"Reason: {appointment.cancellation_reason}" if appointment.cancellation_reason else ""
        )
        subject, body = render("appointment_cancelled", **context)
        send_email.delay(appointment.customer.email, subject, body)
        send_email.delay(appointment.barber.user.email, subject, body)
        return 2


@shared_task(name="app.workers.tasks.notify_appointment_rescheduled")
def notify_appointment_rescheduled(appointment_id: str) -> int:
    with sync_session() as session:
        appointment = _load(session, uuid.UUID(appointment_id))
        if appointment is None:
            return 0
        subject, body = render("appointment_rescheduled", **_context(appointment))
        send_email.delay(appointment.customer.email, subject, body)
        send_email.delay(appointment.barber.user.email, subject, body)
        return 2


@shared_task(name="app.workers.tasks.send_appointment_reminders")
def send_appointment_reminders(now: str | None = None) -> int:
    """Remind customers 24 hours and 2 hours out.

    Idempotency comes from inserting a row into `appointment_reminders` *before*
    queueing the mail: the unique constraint makes a duplicate insert fail, so a
    double beat tick, an overlapping worker or a replayed batch all result in one
    message. A "have we sent one recently?" check over a time window cannot
    promise that, because two workers can both read "no" at the same instant.
    """
    moment = datetime.fromisoformat(now) if now else utcnow()
    queued = 0

    with sync_session() as session:
        for kind, horizon in REMINDER_HORIZONS.items():
            target = moment + horizon
            due = (
                session.execute(
                    select(Appointment)
                    .where(
                        Appointment.status.in_(_ACTIVE),
                        Appointment.start_time >= target,
                        Appointment.start_time < target + REMINDER_WINDOW,
                    )
                    .options(*_eager())
                )
                .scalars()
                .all()
            )

            for appointment in due:
                if _claim_reminder(session, appointment.id, kind):
                    context = _context(appointment)
                    context["horizon"] = (
                        "tomorrow" if kind is ReminderKind.DAY_BEFORE else "in 2 hours"
                    )
                    subject, body = render("appointment_reminder", **context)
                    send_email.delay(appointment.customer.email, subject, body)
                    queued += 1

    return queued


@shared_task(name="app.workers.tasks.expire_stale_pending")
def expire_stale_pending(now: str | None = None) -> int:
    """Release slots held by bookings that were never confirmed."""
    moment = datetime.fromisoformat(now) if now else utcnow()
    cutoff = moment - timedelta(minutes=30)

    with sync_session() as session:
        stale = (
            session.execute(
                select(Appointment).where(
                    Appointment.status == AppointmentStatus.PENDING,
                    Appointment.created_at < cutoff,
                )
            )
            .scalars()
            .all()
        )
        for appointment in stale:
            appointment.status = AppointmentStatus.CANCELLED
            appointment.cancellation_reason = "Not confirmed in time"
        return len(stale)


@shared_task(name="app.workers.tasks.recompute_barber_ratings")
def recompute_barber_ratings() -> int:
    """Nightly drift repair for the denormalized rating.

    The write path already keeps it correct; this exists so that a bug, a manual
    `DELETE`, or a restore from backup cannot leave the column quietly wrong
    forever.
    """
    repaired = 0
    with sync_session() as session:
        barber_ids = session.execute(select(Barber.id)).scalars().all()
        for barber_id in barber_ids:
            if _recompute_one(session, barber_id):
                repaired += 1
    return repaired


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _claim_reminder(session: Session, appointment_id: uuid.UUID, kind: ReminderKind) -> bool:
    """Record the intent to send, returning False if someone already has."""
    savepoint = session.begin_nested()
    try:
        session.add(AppointmentReminder(appointment_id=appointment_id, kind=kind))
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return False
    return True


def _recompute_one(session: Session, barber_id: uuid.UUID) -> bool:
    barber = session.execute(
        select(Barber).where(Barber.id == barber_id).with_for_update()
    ).scalar_one()
    average, count = session.execute(
        select(func.avg(Review.rating), func.count()).where(Review.barber_id == barber_id)
    ).one()

    rating = (
        Decimal("0")
        if not count
        else Decimal(average).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )
    changed = barber.rating != rating or barber.reviews_count != count
    barber.rating = rating
    barber.reviews_count = count
    return changed


def _eager() -> tuple[Any, ...]:
    return (
        selectinload(Appointment.customer),
        selectinload(Appointment.service),
        # `shop` is loaded too: the templates name the shop and its address,
        # and a lazy load in a worker would be an extra query per email.
        selectinload(Appointment.barber).selectinload(Barber.user),
        selectinload(Appointment.barber).selectinload(Barber.shop),
    )


def _load(session: Session, appointment_id: uuid.UUID) -> Appointment | None:
    return session.execute(
        select(Appointment).where(Appointment.id == appointment_id).options(*_eager())
    ).scalar_one_or_none()


def _context(appointment: Appointment) -> dict[str, Any]:
    shop = appointment.barber.shop
    tz = shop_timezone(shop)
    local = appointment.start_time.astimezone(tz)
    return {
        "customer": appointment.customer.first_name,
        "barber": appointment.barber.user.first_name,
        "shop": shop.name,
        "address": shop.address,
        "service": appointment.service.name,
        "price": f"{appointment.total_price:.0f} KZT",
        "when": local.strftime("%A %d %B at %H:%M"),
        "cutoff": settings.APPOINTMENT_CHANGE_CUTOFF_MINUTES,
        "reason": "",
        "horizon": "",
    }
