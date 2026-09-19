"""Turning stored schedules into bookable slots.

The arithmetic lives in `app.services.availability`; this module only fetches
rows, resolves the shop's timezone, and renders the result. Keeping the two
apart is what lets the hard part be tested without a database (§5).
"""

import uuid
from datetime import date as date_type
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.models import (
    Appointment,
    AppointmentStatus,
    Barber,
    BarberBreak,
    Barbershop,
    Service,
    WorkingHours,
)
from app.schemas.availability import AvailabilityRead, ServiceSummary, SlotRead
from app.services.availability import (
    Interval,
    clip,
    compute_free_slots,
    local_day_bounds,
    working_interval,
)
from app.utils.time import utcnow

_ACTIVE_STATUSES = (AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED)


async def available_slots(
    db: AsyncSession,
    barber_id: uuid.UUID,
    *,
    day: date_type,
    service_id: uuid.UUID,
    now: datetime | None = None,
    exclude_appointment: uuid.UUID | None = None,
    ignore_appointments: bool = False,
) -> AvailabilityRead:
    """Free slots for one barber on one local calendar day.

    `exclude_appointment` leaves one booking out of the busy set. Rescheduling
    needs it: without it an appointment blocks the very slots it is trying to
    move within, so nudging a 10:00 cut to 10:15 would report the slot as taken
    by itself.

    `ignore_appointments` answers a different question — "would this time be
    bookable if nobody had taken it?" — which is how the booking service tells
    "you asked for 3 a.m." apart from "someone beat you to it". Never used to
    serve the public endpoint.
    """
    _barber, shop, service = await _load_and_validate(db, barber_id, service_id)
    tz = shop_timezone(shop)

    window = local_day_bounds(day, tz)
    shifts = await _shifts_for(db, barber_id, day, tz)

    if not shifts:
        return _render(day, tz, barber_id, service, [])

    taken = (
        []
        if ignore_appointments
        else await _appointments(db, barber_id, window, exclude=exclude_appointment)
    )
    busy = clip(taken + await _breaks(db, barber_id, window), window)

    slots = compute_free_slots(
        working=clip(shifts, window),
        busy=busy,
        duration=timedelta(minutes=service.duration_minutes),
        step=timedelta(minutes=settings.SLOT_STEP_MINUTES),
        not_before=(now or utcnow()) + timedelta(minutes=settings.MIN_BOOKING_LEAD_MINUTES),
        # The grid is anchored to local midnight, so slots land on :00/:15/:30
        # in the shop's own clock rather than on an arbitrary UTC offset.
        anchor=window.start,
    )
    return _render(day, tz, barber_id, service, slots)


async def _load_and_validate(
    db: AsyncSession, barber_id: uuid.UUID, service_id: uuid.UUID
) -> tuple[Barber, Barbershop, Service]:
    barber = (
        await db.execute(
            select(Barber)
            .where(Barber.id == barber_id, Barber.is_active.is_(True))
            .options(selectinload(Barber.services))
        )
    ).scalar_one_or_none()
    if barber is None:
        raise NotFoundError("Barber not found")

    shop = await db.get(Barbershop, barber.shop_id)
    if shop is None or not shop.is_active:
        raise NotFoundError("Barber not found")

    service = await db.get(Service, service_id)
    if service is None or not service.is_active:
        raise NotFoundError("Service not found")

    # Both checks matter: a service from another shop, and a service this shop
    # offers but this particular barber does not perform.
    if service.shop_id != barber.shop_id:
        raise ValidationError("That service belongs to a different barbershop")
    if service.id not in {s.id for s in barber.services}:
        raise ValidationError(
            "This barber does not perform that service",
            details={"barber_id": str(barber_id), "service_id": str(service_id)},
        )
    return barber, shop, service


async def _shifts_for(
    db: AsyncSession, barber_id: uuid.UUID, day: date_type, tz: ZoneInfo
) -> list[Interval]:
    rows = (
        (
            await db.execute(
                select(WorkingHours).where(
                    WorkingHours.barber_id == barber_id,
                    # `date.weekday()` is already 0=Monday, matching the column.
                    WorkingHours.weekday == day.weekday(),
                )
            )
        )
        .scalars()
        .all()
    )
    shifts = [working_interval(day, row.start_time, row.end_time, tz) for row in rows]
    return [shift for shift in shifts if shift is not None]


async def _appointments(
    db: AsyncSession,
    barber_id: uuid.UUID,
    window: Interval,
    *,
    exclude: uuid.UUID | None = None,
) -> list[Interval]:
    stmt = select(Appointment.start_time, Appointment.end_time).where(
        Appointment.barber_id == barber_id,
        Appointment.status.in_(_ACTIVE_STATUSES),
        Appointment.start_time < window.end,
        Appointment.end_time > window.start,
    )
    if exclude is not None:
        stmt = stmt.where(Appointment.id != exclude)
    rows = (await db.execute(stmt)).tuples().all()
    return [Interval(start, end) for start, end in rows]


async def _breaks(db: AsyncSession, barber_id: uuid.UUID, window: Interval) -> list[Interval]:
    rows = (
        (
            await db.execute(
                select(BarberBreak.start_datetime, BarberBreak.end_datetime).where(
                    BarberBreak.barber_id == barber_id,
                    BarberBreak.start_datetime < window.end,
                    BarberBreak.end_datetime > window.start,
                )
            )
        )
        .tuples()
        .all()
    )
    return [Interval(start, end) for start, end in rows]


def shop_timezone(shop: Barbershop) -> ZoneInfo:
    """The shop's IANA zone, falling back to the configured default."""
    try:
        return ZoneInfo(shop.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        # Stored values are validated on write, so this only fires if the tz
        # database drops a zone. Falling back beats failing the whole request.
        return settings.default_tz


def _render(
    day: date_type,
    tz: ZoneInfo,
    barber_id: uuid.UUID,
    service: Service,
    slots: list[Interval],
) -> AvailabilityRead:
    return AvailabilityRead(
        date=day,
        timezone=str(tz),
        barber_id=barber_id,
        service=ServiceSummary(
            id=service.id, name=service.name, duration_minutes=service.duration_minutes
        ),
        slots=[
            SlotRead(
                start=slot.start,
                end=slot.end,
                start_local=slot.start.astimezone(tz).strftime("%H:%M"),
                end_local=slot.end.astimezone(tz).strftime("%H:%M"),
            )
            for slot in slots
        ],
    )
