"""Creating, moving, and closing out appointments.

Two rules carry this module:

* **What the API offers is exactly what it accepts.** A booking is checked
  against the same slot computation the availability endpoint serves, so a
  customer can never book a time that was never on offer.
* **The database arbitrates races, not this code.** The overlap check below is
  there to produce a friendly error on the ordinary path; when two customers
  genuinely race, the `no_double_booking` exclusion constraint decides, and the
  loser's `23P01` becomes a `409 SLOT_TAKEN` (see docs/SPEC.md §5).
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.base import ExecutableOption
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import settings
from app.core.errors import (
    CutoffPassedError,
    ForbiddenError,
    InvalidTransitionError,
    NotFoundError,
    OutsideWorkingHoursError,
    SlotTakenError,
    ValidationError,
)
from app.core.pagination import Page, PageParams
from app.models import (
    Appointment,
    AppointmentStatus,
    Barber,
    Barbershop,
    Service,
    User,
    UserRole,
)
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentFilters,
    AppointmentRead,
    PersonSummary,
)
from app.schemas.availability import ServiceSummary
from app.services import notifications
from app.services import slots as slots_service
from app.services.permissions import get_shop_or_404
from app.utils.time import utcnow

#: SQLSTATE for an exclusion-constraint violation.
_EXCLUSION_VIOLATION = "23P01"

_ACTIVE_STATUSES = (AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED)


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #


async def create_appointment(
    db: AsyncSession, customer: User, payload: AppointmentCreate, *, now: datetime | None = None
) -> AppointmentRead:
    moment = now or utcnow()
    barber, service = await _resolve_barber_and_service(db, payload.barber_id, payload.service_id)
    shop = await db.get(Barbershop, barber.shop_id)
    start = payload.start_time
    end = start + timedelta(minutes=service.duration_minutes)

    await _assert_on_offer(db, barber, service, start, moment)

    appointment = Appointment(
        customer_id=customer.id,
        barber_id=barber.id,
        service_id=service.id,
        start_time=start,
        end_time=end,
        # A shop that vets its bookings gets PENDING; everyone else gets a
        # booking that is simply made. Either way the slot is held, so the shop
        # cannot sell the same time twice while it decides.
        status=(
            AppointmentStatus.PENDING
            if shop is not None and shop.requires_confirmation
            else AppointmentStatus.CONFIRMED
        ),
        # Snapshot: a later price change must not rewrite this booking or the
        # revenue reports built from it.
        total_price=service.price,
    )
    db.add(appointment)
    await _commit_or_slot_taken(db)
    notifications.appointment_event("created", appointment.id)
    return await get_appointment(db, customer, appointment.id)


# --------------------------------------------------------------------------- #
# transitions
# --------------------------------------------------------------------------- #


async def cancel_appointment(
    db: AsyncSession,
    user: User,
    appointment_id: uuid.UUID,
    *,
    reason: str | None = None,
    now: datetime | None = None,
) -> AppointmentRead:
    moment = now or utcnow()
    appointment, is_staff = await _load_for_action(db, user, appointment_id)

    if appointment.status not in _ACTIVE_STATUSES:
        raise InvalidTransitionError(
            f"A {appointment.status.value} appointment cannot be cancelled",
            details={"status": appointment.status.value},
        )
    _assert_within_cutoff(appointment, moment, is_staff=is_staff)

    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancelled_by_id = user.id
    appointment.cancellation_reason = reason
    await db.commit()
    notifications.appointment_event("cancelled", appointment_id)
    return await get_appointment(db, user, appointment_id)


async def reschedule_appointment(
    db: AsyncSession,
    user: User,
    appointment_id: uuid.UUID,
    new_start: datetime,
    *,
    now: datetime | None = None,
) -> AppointmentRead:
    """Move an appointment, keeping the same row.

    Re-runs the full availability check at the new time, and the exclusion
    constraint applies again. The row does not conflict with itself, because the
    constraint compares a row against the *other* rows.
    """
    moment = now or utcnow()
    appointment, is_staff = await _load_for_action(db, user, appointment_id)

    if appointment.status not in _ACTIVE_STATUSES:
        raise InvalidTransitionError(
            f"A {appointment.status.value} appointment cannot be rescheduled",
            details={"status": appointment.status.value},
        )
    _assert_within_cutoff(appointment, moment, is_staff=is_staff)

    barber, service = await _resolve_barber_and_service(
        db, appointment.barber_id, appointment.service_id
    )
    await _assert_on_offer(db, barber, service, new_start, moment, exclude=appointment.id)

    appointment.start_time = new_start
    appointment.end_time = new_start + timedelta(minutes=service.duration_minutes)
    await _commit_or_slot_taken(db)
    notifications.appointment_event("rescheduled", appointment_id)
    return await get_appointment(db, user, appointment_id)


async def confirm_appointment(
    db: AsyncSession, user: User, appointment_id: uuid.UUID
) -> AppointmentRead:
    """Accept a booking a shop asked to vet first.

    Only meaningful where `barbershops.requires_confirmation` is set; elsewhere
    nothing is ever PENDING and this has nothing to act on.
    """
    appointment, is_staff = await _load_for_action(db, user, appointment_id)

    if not is_staff:
        raise ForbiddenError("Only the barber or the shop owner can confirm a booking")
    if appointment.status is not AppointmentStatus.PENDING:
        raise InvalidTransitionError(
            f"A {appointment.status.value} appointment cannot be confirmed",
            details={"status": appointment.status.value},
        )

    appointment.status = AppointmentStatus.CONFIRMED
    await db.commit()
    notifications.appointment_event("created", appointment_id)
    return await get_appointment(db, user, appointment_id)


async def complete_appointment(
    db: AsyncSession, user: User, appointment_id: uuid.UUID, *, now: datetime | None = None
) -> AppointmentRead:
    return await _close_out(db, user, appointment_id, AppointmentStatus.COMPLETED, now)


async def mark_no_show(
    db: AsyncSession, user: User, appointment_id: uuid.UUID, *, now: datetime | None = None
) -> AppointmentRead:
    return await _close_out(db, user, appointment_id, AppointmentStatus.NO_SHOW, now)


async def _close_out(
    db: AsyncSession,
    user: User,
    appointment_id: uuid.UUID,
    target: AppointmentStatus,
    now: datetime | None,
) -> AppointmentRead:
    moment = now or utcnow()
    appointment, is_staff = await _load_for_action(db, user, appointment_id)

    if not is_staff:
        raise ForbiddenError("Only the barber or the shop owner can close out an appointment")
    # PENDING counts too: a customer who turns up to an unvetted booking was
    # still served, and a no-show is still a no-show.
    if appointment.status not in _ACTIVE_STATUSES:
        raise InvalidTransitionError(
            f"A {appointment.status.value} appointment cannot become {target.value}",
            details={"status": appointment.status.value},
        )
    if appointment.start_time > moment:
        # Otherwise a barber could mark tomorrow's haircut as done today, and the
        # revenue figures would count work that has not happened.
        raise InvalidTransitionError(
            "This appointment has not started yet",
            details={"starts_at": appointment.start_time.isoformat()},
        )

    appointment.status = target
    await db.commit()
    return await get_appointment(db, user, appointment_id)


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


async def get_appointment(
    db: AsyncSession, user: User, appointment_id: uuid.UUID
) -> AppointmentRead:
    appointment = await _load(db, appointment_id)
    if not await _may_view(db, user, appointment):
        raise ForbiddenError("You are not part of this appointment")
    return _to_read(appointment)


async def list_my_appointments(
    db: AsyncSession,
    user: User,
    params: PageParams,
    filters: AppointmentFilters,
    *,
    as_barber: bool = False,
) -> Page[AppointmentRead]:
    condition: ColumnElement[bool]
    if as_barber:
        barber_ids = (
            (await db.execute(select(Barber.id).where(Barber.user_id == user.id))).scalars().all()
        )
        if not barber_ids:
            raise ForbiddenError("You do not have a barber profile")
        condition = Appointment.barber_id.in_(barber_ids)
    else:
        condition = Appointment.customer_id == user.id

    return await _paginate(db, select(Appointment).where(condition), params, filters)


async def list_shop_appointments(
    db: AsyncSession,
    user: User,
    shop_id: uuid.UUID,
    params: PageParams,
    filters: AppointmentFilters,
) -> Page[AppointmentRead]:
    shop = await get_shop_or_404(db, shop_id, viewer=user)
    if user.role is not UserRole.ADMIN and shop.owner_id != user.id:
        raise ForbiddenError("You do not own this barbershop")

    stmt = (
        select(Appointment)
        .join(Barber, Barber.id == Appointment.barber_id)
        .where(Barber.shop_id == shop_id)
    )
    return await _paginate(db, stmt, params, filters)


async def _paginate(
    db: AsyncSession,
    stmt: Select[tuple[Appointment]],
    params: PageParams,
    filters: AppointmentFilters,
) -> Page[AppointmentRead]:
    stmt = _apply_filters(stmt, filters)

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (
            await db.execute(
                stmt.options(*_eager())
                .order_by(Appointment.start_time.desc(), Appointment.id)
                .offset(params.offset)
                .limit(params.limit)
            )
        )
        .scalars()
        .all()
    )
    return Page.build([_to_read(row) for row in rows], total, params)


def _apply_filters(
    stmt: Select[tuple[Appointment]], filters: AppointmentFilters
) -> Select[tuple[Appointment]]:
    if filters.status is not None:
        stmt = stmt.where(Appointment.status == filters.status)
    if filters.date_from is not None:
        stmt = stmt.where(Appointment.end_time > filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(Appointment.start_time < filters.date_to)
    if filters.barber_id is not None:
        stmt = stmt.where(Appointment.barber_id == filters.barber_id)
    return stmt


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


async def _assert_on_offer(
    db: AsyncSession,
    barber: Barber,
    service: Service,
    start: datetime,
    now: datetime,
    *,
    exclude: uuid.UUID | None = None,
) -> None:
    """Refuse any start time the availability endpoint would not have shown.

    Checking membership in the offered slots — rather than re-deriving a weaker
    "does it overlap anything" rule — is what keeps the two endpoints honest
    with each other: grid alignment, lead time, breaks, working hours and the
    barber's service list are all enforced by construction.
    """
    shop = await db.get(Barbershop, barber.shop_id)
    assert shop is not None
    local_day = start.astimezone(slots_service.shop_timezone(shop)).date()

    offer = await slots_service.available_slots(
        db,
        barber.id,
        day=local_day,
        service_id=service.id,
        now=now,
        exclude_appointment=exclude,
    )
    if any(slot.start == start for slot in offer.slots):
        return

    # Distinguish the two ways a time can be unavailable. Without this, a
    # customer who loses a race gets "outside working hours" whenever the
    # winner's commit lands before this check — the same race would report
    # SLOT_TAKEN or OUTSIDE_WORKING_HOURS depending purely on timing.
    if_empty = await slots_service.available_slots(
        db,
        barber.id,
        day=local_day,
        service_id=service.id,
        now=now,
        ignore_appointments=True,
    )
    if any(slot.start == start for slot in if_empty.slots):
        raise SlotTakenError()

    raise OutsideWorkingHoursError(
        "That time is not available for this barber",
        details={
            "requested": start.isoformat(),
            "date": local_day.isoformat(),
            "timezone": offer.timezone,
            "available": [slot.start.isoformat() for slot in offer.slots[:20]],
        },
    )


async def _commit_or_slot_taken(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if getattr(exc.orig, "sqlstate", None) == _EXCLUSION_VIOLATION:
            raise SlotTakenError() from exc
        raise


async def _resolve_barber_and_service(
    db: AsyncSession, barber_id: uuid.UUID, service_id: uuid.UUID
) -> tuple[Barber, Service]:
    barber = (
        await db.execute(
            select(Barber)
            .where(Barber.id == barber_id, Barber.is_active.is_(True))
            .options(selectinload(Barber.services))
        )
    ).scalar_one_or_none()
    if barber is None:
        raise NotFoundError("Barber not found")

    service = await db.get(Service, service_id)
    if service is None or not service.is_active:
        raise NotFoundError("Service not found")
    if service.shop_id != barber.shop_id:
        raise ValidationError("That service belongs to a different barbershop")
    if service.id not in {s.id for s in barber.services}:
        raise ValidationError("This barber does not perform that service")
    return barber, service


def _assert_within_cutoff(appointment: Appointment, now: datetime, *, is_staff: bool) -> None:
    """Customers lose the right to change a booking shortly before it starts.

    Staff are exempt: a barber who is off sick has to be able to cancel at short
    notice, and the customer keeps no such power (§4).
    """
    if is_staff:
        return
    cutoff = appointment.start_time - timedelta(minutes=settings.APPOINTMENT_CHANGE_CUTOFF_MINUTES)
    if now >= cutoff:
        raise CutoffPassedError(
            "It is too late to change this appointment; please call the shop",
            details={
                "cutoff_minutes": settings.APPOINTMENT_CHANGE_CUTOFF_MINUTES,
                "starts_at": appointment.start_time.isoformat(),
            },
        )


async def _load(db: AsyncSession, appointment_id: uuid.UUID) -> Appointment:
    appointment = (
        await db.execute(
            select(Appointment).where(Appointment.id == appointment_id).options(*_eager())
        )
    ).scalar_one_or_none()
    if appointment is None:
        raise NotFoundError("Appointment not found")
    return appointment


async def _load_for_action(
    db: AsyncSession, user: User, appointment_id: uuid.UUID
) -> tuple[Appointment, bool]:
    """Return the appointment plus whether the caller is acting as staff."""
    appointment = await _load(db, appointment_id)
    is_staff = await _is_staff_for(db, user, appointment)
    if not is_staff and appointment.customer_id != user.id:
        raise ForbiddenError("You are not part of this appointment")
    return appointment, is_staff


async def _is_staff_for(db: AsyncSession, user: User, appointment: Appointment) -> bool:
    if user.role is UserRole.ADMIN:
        return True
    if appointment.barber.user_id == user.id:
        return True
    shop = await db.get(Barbershop, appointment.barber.shop_id)
    return shop is not None and shop.owner_id == user.id


async def _may_view(db: AsyncSession, user: User, appointment: Appointment) -> bool:
    return appointment.customer_id == user.id or await _is_staff_for(db, user, appointment)


def _eager() -> tuple[ExecutableOption, ...]:
    return (
        selectinload(Appointment.customer),
        selectinload(Appointment.service),
        selectinload(Appointment.barber).selectinload(Barber.user),
    )


def _to_read(appointment: Appointment) -> AppointmentRead:
    return AppointmentRead(
        id=appointment.id,
        customer=PersonSummary(
            id=appointment.customer.id,
            first_name=appointment.customer.first_name,
            last_name=appointment.customer.last_name,
        ),
        barber_id=appointment.barber_id,
        barber=PersonSummary(
            id=appointment.barber.id,
            first_name=appointment.barber.user.first_name,
            last_name=appointment.barber.user.last_name,
        ),
        shop_id=appointment.barber.shop_id,
        service=ServiceSummary(
            id=appointment.service.id,
            name=appointment.service.name,
            duration_minutes=appointment.service.duration_minutes,
        ),
        start_time=appointment.start_time,
        end_time=appointment.end_time,
        status=appointment.status,
        total_price=appointment.total_price,
        cancellation_reason=appointment.cancellation_reason,
        created_at=appointment.created_at,
    )
