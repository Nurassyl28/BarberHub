"""Barbers: joining a shop, their profile, and their service assignments."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.base import ExecutableOption

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import Appointment, AppointmentStatus, Barber, Service, User, UserRole
from app.schemas.barber import BarberCreate, BarberRead, BarberServicesUpdate, BarberUpdate
from app.schemas.service import ServiceRead
from app.services.permissions import (
    ensure_can_manage_barber,
    ensure_can_manage_shop,
    get_shop_or_404,
)
from app.utils.time import utcnow

_ACTIVE_STATUSES = (AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED)


def _loaded() -> tuple[ExecutableOption, ...]:
    """Eager-load what `BarberRead` needs, so serialization never lazy-loads.

    A lazy load on an async session raises `MissingGreenlet` rather than
    quietly issuing a query, so this is load-bearing, not an optimization.
    """
    return selectinload(Barber.user), selectinload(Barber.services)


def _to_read(barber: Barber) -> BarberRead:
    """Flatten the barber and their account into one response object.

    Built field by field rather than via `model_validate` + `model_copy`:
    validation runs first, so the name fields — which live on `users`, not
    `barbers` — would be missing at that point.
    """
    return BarberRead(
        id=barber.id,
        user_id=barber.user_id,
        shop_id=barber.shop_id,
        first_name=barber.user.first_name,
        last_name=barber.user.last_name,
        bio=barber.bio,
        experience_years=barber.experience_years,
        rating=barber.rating,
        reviews_count=barber.reviews_count,
        is_active=barber.is_active,
        services=[ServiceRead.model_validate(s) for s in barber.services if s.is_active],
    )


async def list_barbers(
    db: AsyncSession, shop_id: uuid.UUID, *, viewer: User | None
) -> list[BarberRead]:
    shop = await get_shop_or_404(db, shop_id, viewer=viewer)
    stmt = (
        select(Barber)
        .where(Barber.shop_id == shop.id)
        .options(*_loaded())
        .order_by(Barber.rating.desc(), Barber.id)
    )
    if not (viewer and (viewer.role is UserRole.ADMIN or shop.owner_id == viewer.id)):
        stmt = stmt.where(Barber.is_active.is_(True))
    return [_to_read(b) for b in (await db.execute(stmt)).scalars().all()]


async def get_barber(db: AsyncSession, barber_id: uuid.UUID) -> BarberRead:
    return _to_read(await _load(db, barber_id))


async def add_barber(
    db: AsyncSession, user: User, shop_id: uuid.UUID, payload: BarberCreate
) -> BarberRead:
    shop = await get_shop_or_404(db, shop_id, viewer=user)
    ensure_can_manage_shop(user, shop)

    account = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()
    if account is None:
        raise NotFoundError(
            "No account with that email. The barber must register first.",
            details={"email": payload.email},
        )
    if not account.is_active:
        raise ConflictError("That account has been deactivated")

    barber = Barber(
        user_id=account.id,
        shop_id=shop.id,
        bio=payload.bio,
        experience_years=payload.experience_years,
    )
    db.add(barber)

    # Promote, never demote: a shop owner who also cuts hair keeps SHOP_OWNER,
    # which outranks BARBER, and an admin is left alone entirely.
    if account.role is UserRole.CUSTOMER:
        account.role = UserRole.BARBER

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("This person is already a barber at this shop") from exc

    return _to_read(await _load(db, barber.id))


async def update_barber(
    db: AsyncSession, user: User, barber_id: uuid.UUID, payload: BarberUpdate
) -> BarberRead:
    barber = await _load(db, barber_id, include_inactive=True)
    await ensure_can_manage_barber(db, user, barber)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(barber, field, value)

    await db.commit()
    return _to_read(await _load(db, barber_id, include_inactive=True))


async def deactivate_barber(db: AsyncSession, user: User, barber_id: uuid.UUID) -> None:
    barber = await _load(db, barber_id, include_inactive=True)
    shop = await get_shop_or_404(db, barber.shop_id, viewer=user)
    # Deliberately stricter than `update`: a barber may edit their own bio but
    # may not remove themselves and strand booked customers.
    ensure_can_manage_shop(user, shop)

    upcoming = (
        await db.execute(
            select(func.count())
            .select_from(Appointment)
            .where(
                Appointment.barber_id == barber.id,
                Appointment.start_time >= utcnow(),
                Appointment.status.in_(_ACTIVE_STATUSES),
            )
        )
    ).scalar_one()
    if upcoming:
        raise ConflictError(
            "This barber still has upcoming appointments",
            details={"upcoming_appointments": upcoming},
        )

    barber.is_active = False
    await db.commit()


async def set_services(
    db: AsyncSession, user: User, barber_id: uuid.UUID, payload: BarberServicesUpdate
) -> BarberRead:
    """Replace the barber's service list wholesale."""
    barber = await _load(db, barber_id, include_inactive=True)
    shop = await get_shop_or_404(db, barber.shop_id, viewer=user)
    ensure_can_manage_shop(user, shop)

    wanted = list(dict.fromkeys(payload.service_ids))  # de-dupe, keep order
    services = await _resolve_services(db, barber, wanted)

    barber.services = services
    await db.commit()
    return _to_read(await _load(db, barber_id, include_inactive=True))


async def _resolve_services(
    db: AsyncSession, barber: Barber, service_ids: Sequence[uuid.UUID]
) -> list[Service]:
    """Load the requested services, rejecting anything not sellable by this barber.

    A CHECK constraint cannot express "same shop" because it spans two tables,
    so this is where that rule actually lives (see docs/SPEC.md §3).
    """
    if not service_ids:
        return []

    found = list(
        (await db.execute(select(Service).where(Service.id.in_(service_ids)))).scalars().all()
    )
    by_id = {s.id: s for s in found}

    missing = [str(sid) for sid in service_ids if sid not in by_id]
    foreign = [str(s.id) for s in found if s.shop_id != barber.shop_id]
    inactive = [str(s.id) for s in found if s.shop_id == barber.shop_id and not s.is_active]

    if missing or foreign or inactive:
        raise ValidationError(
            "Some services cannot be assigned to this barber",
            details={
                "unknown": missing,
                "belong_to_another_shop": foreign,
                "inactive": inactive,
            },
        )
    return [by_id[sid] for sid in service_ids]


async def _load(
    db: AsyncSession, barber_id: uuid.UUID, *, include_inactive: bool = False
) -> Barber:
    stmt = select(Barber).where(Barber.id == barber_id).options(*_loaded())
    if not include_inactive:
        stmt = stmt.where(Barber.is_active.is_(True))
    barber = (await db.execute(stmt)).scalar_one_or_none()
    if barber is None:
        raise NotFoundError("Barber not found")
    return barber
