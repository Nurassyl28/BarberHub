"""Working hours and breaks.

This is the data the availability engine consumes: the weekly template
(`working_hours`) minus the one-off blocks (`barber_breaks`).
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Appointment, AppointmentStatus, Barber, BarberBreak, User, WorkingHours
from app.schemas.schedule import (
    BreakCreate,
    BreakRead,
    WorkingHoursRead,
    WorkingHoursUpdate,
)
from app.services.permissions import ensure_can_manage_barber

_ACTIVE_STATUSES = (AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED)


async def list_working_hours(db: AsyncSession, barber_id: uuid.UUID) -> list[WorkingHoursRead]:
    await _get_barber(db, barber_id)
    rows = (
        (
            await db.execute(
                select(WorkingHours)
                .where(WorkingHours.barber_id == barber_id)
                .order_by(WorkingHours.weekday, WorkingHours.start_time)
            )
        )
        .scalars()
        .all()
    )
    return [WorkingHoursRead.model_validate(row) for row in rows]


async def replace_working_hours(
    db: AsyncSession, user: User, barber_id: uuid.UUID, payload: WorkingHoursUpdate
) -> list[WorkingHoursRead]:
    """Swap the whole week in one transaction.

    Delete-then-insert rather than a diff: the payload *is* the new schedule, and
    a partial failure would leave a half-applied week, which is worse than either
    outcome. Existing appointments are deliberately left alone — they were agreed
    with a customer and are not invalidated by a template change.
    """
    barber = await _get_barber(db, barber_id)
    await ensure_can_manage_barber(db, user, barber)

    await db.execute(delete(WorkingHours).where(WorkingHours.barber_id == barber_id))
    db.add_all(
        [
            WorkingHours(
                barber_id=barber_id,
                weekday=slot.weekday,
                start_time=slot.start_time,
                end_time=slot.end_time,
            )
            for slot in payload.items
        ]
    )
    await db.commit()
    return await list_working_hours(db, barber_id)


async def list_breaks(
    db: AsyncSession,
    user: User,
    barber_id: uuid.UUID,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[BreakRead]:
    barber = await _get_barber(db, barber_id)
    await ensure_can_manage_barber(db, user, barber)

    stmt = select(BarberBreak).where(BarberBreak.barber_id == barber_id)
    if date_from is not None:
        stmt = stmt.where(BarberBreak.end_datetime > date_from)
    if date_to is not None:
        stmt = stmt.where(BarberBreak.start_datetime < date_to)

    rows = (await db.execute(stmt.order_by(BarberBreak.start_datetime))).scalars().all()
    return [BreakRead.model_validate(row) for row in rows]


async def create_break(
    db: AsyncSession, user: User, barber_id: uuid.UUID, payload: BreakCreate
) -> BreakRead:
    barber = await _get_barber(db, barber_id)
    await ensure_can_manage_barber(db, user, barber)

    clashing = await _appointments_overlapping(
        db, barber_id, payload.start_datetime, payload.end_datetime
    )
    if clashing:
        # Blocking time a customer has already booked would silently orphan that
        # appointment: it would still exist but sit inside "unavailable" time.
        raise ConflictError(
            "This break overlaps appointments that are already booked",
            details={"conflicting_appointments": clashing},
        )

    barber_break = BarberBreak(
        barber_id=barber_id,
        start_datetime=payload.start_datetime,
        end_datetime=payload.end_datetime,
        reason=payload.reason,
    )
    db.add(barber_break)
    await db.commit()
    await db.refresh(barber_break)
    return BreakRead.model_validate(barber_break)


async def delete_break(db: AsyncSession, user: User, break_id: uuid.UUID) -> None:
    barber_break = await db.get(BarberBreak, break_id)
    if barber_break is None:
        raise NotFoundError("Break not found")

    barber = await _get_barber(db, barber_break.barber_id)
    await ensure_can_manage_barber(db, user, barber)

    await db.delete(barber_break)
    await db.commit()


async def _appointments_overlapping(
    db: AsyncSession, barber_id: uuid.UUID, start: datetime, end: datetime
) -> list[str]:
    """Half-open `[start, end)` overlap, matching the exclusion constraint."""
    rows = (
        (
            await db.execute(
                select(Appointment.id).where(
                    Appointment.barber_id == barber_id,
                    Appointment.status.in_(_ACTIVE_STATUSES),
                    Appointment.start_time < end,
                    Appointment.end_time > start,
                )
            )
        )
        .scalars()
        .all()
    )
    return [str(row) for row in rows]


async def _get_barber(db: AsyncSession, barber_id: uuid.UUID) -> Barber:
    barber = await db.get(Barber, barber_id)
    if barber is None:
        raise NotFoundError("Barber not found")
    return barber


async def count_working_hours(db: AsyncSession, barber_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(WorkingHours).where(WorkingHours.barber_id == barber_id)
    )
    return result.scalar_one()
