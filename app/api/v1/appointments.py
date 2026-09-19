"""Appointment endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, require_roles
from app.core.pagination import Page, PageParamsDep
from app.db.session import DbSession
from app.models import User, UserRole
from app.schemas.appointment import (
    AppointmentCancel,
    AppointmentCreate,
    AppointmentFilters,
    AppointmentRead,
    AppointmentReschedule,
)
from app.services import booking

router = APIRouter(prefix="/appointments", tags=["appointments"])
shop_scoped = APIRouter(prefix="/barbershops/{shop_id}/appointments", tags=["appointments"])

FiltersDep = Annotated[AppointmentFilters, Depends()]
CustomerOnly = Depends(require_roles(UserRole.CUSTOMER))


@router.post(
    "",
    response_model=AppointmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Book an appointment",
    description=(
        "`start_time` must be one of the starts returned by the barber's "
        "`available-slots` for that service, and must carry a timezone offset.\n\n"
        "Returns **409 SLOT_TAKEN** when another customer booked the same time "
        "first — the database, not the application, decides that race."
    ),
)
async def create_appointment(
    payload: AppointmentCreate, db: DbSession, customer: User = CustomerOnly
) -> AppointmentRead:
    return await booking.create_appointment(db, customer, payload)


@router.get(
    "/me",
    response_model=Page[AppointmentRead],
    summary="The caller's appointments",
    description="By default the caller's own bookings as a customer. "
    "Pass `as_barber=true` to see the schedule they work.",
)
async def my_appointments(
    db: DbSession,
    user: CurrentUser,
    params: PageParamsDep,
    filters: FiltersDep,
    as_barber: Annotated[bool, Query(description="View the caller's barber schedule")] = False,
) -> Page[AppointmentRead]:
    return await booking.list_my_appointments(db, user, params, filters, as_barber=as_barber)


@router.get(
    "/{appointment_id}",
    response_model=AppointmentRead,
    summary="One appointment",
    description="Visible to the customer, the barber, the shop owner, and admins.",
)
async def get_appointment(
    appointment_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> AppointmentRead:
    return await booking.get_appointment(db, user, appointment_id)


@router.patch(
    "/{appointment_id}/cancel",
    response_model=AppointmentRead,
    summary="Cancel an appointment",
    description="Customers may cancel until the change cutoff; staff any time.",
)
async def cancel_appointment(
    appointment_id: uuid.UUID,
    payload: AppointmentCancel,
    db: DbSession,
    user: CurrentUser,
) -> AppointmentRead:
    return await booking.cancel_appointment(db, user, appointment_id, reason=payload.reason)


@router.patch(
    "/{appointment_id}/reschedule",
    response_model=AppointmentRead,
    summary="Move an appointment",
    description="Same booking, new time. The new time is validated exactly like a fresh booking.",
)
async def reschedule_appointment(
    appointment_id: uuid.UUID,
    payload: AppointmentReschedule,
    db: DbSession,
    user: CurrentUser,
) -> AppointmentRead:
    return await booking.reschedule_appointment(db, user, appointment_id, payload.start_time)


@router.patch(
    "/{appointment_id}/complete",
    response_model=AppointmentRead,
    summary="Mark an appointment completed",
    description="Barber, shop owner, or admin, and only once it has started.",
)
async def complete_appointment(
    appointment_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> AppointmentRead:
    return await booking.complete_appointment(db, user, appointment_id)


@router.patch(
    "/{appointment_id}/no-show",
    response_model=AppointmentRead,
    summary="Mark the customer as a no-show",
)
async def no_show_appointment(
    appointment_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> AppointmentRead:
    return await booking.mark_no_show(db, user, appointment_id)


@shop_scoped.get(
    "",
    response_model=Page[AppointmentRead],
    summary="Every appointment in a shop",
    description="Shop owner or admin. Filter by status, barber, and date range.",
)
async def shop_appointments(
    shop_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    params: PageParamsDep,
    filters: FiltersDep,
) -> Page[AppointmentRead]:
    return await booking.list_shop_appointments(db, user, shop_id, params, filters)
