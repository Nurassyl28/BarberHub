"""Shop services (the catalogue, not the business-logic layer)."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Appointment, AppointmentStatus, Barbershop, Service, User, UserRole
from app.schemas.service import ServiceCreate, ServiceRead, ServiceUpdate
from app.services.permissions import ensure_can_manage_shop, get_shop_or_404
from app.utils.time import utcnow

_ACTIVE_STATUSES = (AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED)


async def list_services(
    db: AsyncSession, shop_id: uuid.UUID, *, viewer: User | None
) -> list[ServiceRead]:
    shop = await get_shop_or_404(db, shop_id, viewer=viewer)
    stmt = select(Service).where(Service.shop_id == shop.id)
    # Retired services stay visible to staff so they can be reactivated; the
    # public only sees what can actually be booked.
    if not _is_staff(viewer, shop):
        stmt = stmt.where(Service.is_active.is_(True))
    rows = (await db.execute(stmt.order_by(Service.name))).scalars().all()
    return [ServiceRead.model_validate(row) for row in rows]


async def create_service(
    db: AsyncSession, user: User, shop_id: uuid.UUID, payload: ServiceCreate
) -> ServiceRead:
    shop = await get_shop_or_404(db, shop_id, viewer=user)
    ensure_can_manage_shop(user, shop)

    service = Service(shop_id=shop.id, **payload.model_dump())
    db.add(service)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(f"This shop already offers a service named {payload.name!r}") from exc
    await db.refresh(service)
    return ServiceRead.model_validate(service)


async def update_service(
    db: AsyncSession, user: User, service_id: uuid.UUID, payload: ServiceUpdate
) -> ServiceRead:
    service = await _get_manageable_service(db, user, service_id)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(service, field, value)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("This shop already offers a service with that name") from exc
    await db.refresh(service)
    return ServiceRead.model_validate(service)


async def deactivate_service(db: AsyncSession, user: User, service_id: uuid.UUID) -> None:
    """Soft delete.

    Hard deletion is not an option: `appointments.service_id` is `ON DELETE
    RESTRICT` precisely so past bookings keep pointing at what was actually
    sold (§11.9).
    """
    service = await _get_manageable_service(db, user, service_id)

    upcoming = (
        await db.execute(
            select(func.count())
            .select_from(Appointment)
            .where(
                Appointment.service_id == service.id,
                Appointment.start_time >= utcnow(),
                Appointment.status.in_(_ACTIVE_STATUSES),
            )
        )
    ).scalar_one()
    if upcoming:
        raise ConflictError(
            "This service still has upcoming appointments",
            details={"upcoming_appointments": upcoming},
        )

    service.is_active = False
    await db.commit()


async def _get_manageable_service(db: AsyncSession, user: User, service_id: uuid.UUID) -> Service:
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("Service not found")
    shop = await get_shop_or_404(db, service.shop_id, viewer=user)
    ensure_can_manage_shop(user, shop)
    return service


def _is_staff(viewer: User | None, shop: Barbershop) -> bool:
    return viewer is not None and (viewer.role is UserRole.ADMIN or shop.owner_id == viewer.id)
