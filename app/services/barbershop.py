"""Barbershop reads and writes."""

import uuid
from typing import Any

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.pagination import Page, PageParams
from app.models import Appointment, AppointmentStatus, Barber, Barbershop, Service, User
from app.schemas.barbershop import BarbershopCreate, BarbershopRead, BarbershopUpdate
from app.schemas.search import ShopFilters, ShopSort
from app.services.geo import bounding_box, haversine_km
from app.services.permissions import ensure_can_manage_shop, get_shop_or_404
from app.utils.time import utcnow


def _aggregates() -> tuple[Any, Any, Any]:
    """Correlated subqueries for the numbers shown on every shop card.

    Kept as subqueries rather than a JOIN + GROUP BY so the shop row itself is
    never duplicated, and so paging stays a plain LIMIT over `barbershops`.
    """
    rated = (Barber.shop_id == Barbershop.id) & Barber.is_active.is_(True)
    rating = (
        select(func.coalesce(func.round(func.avg(Barber.rating), 2), 0))
        .where(rated, Barber.reviews_count > 0)
        .correlate(Barbershop)
        .scalar_subquery()
    )
    reviews = (
        select(func.coalesce(func.sum(Barber.reviews_count), 0))
        .where(rated)
        .correlate(Barbershop)
        .scalar_subquery()
    )
    barbers = (
        select(func.count())
        .select_from(Barber)
        .where(rated)
        .correlate(Barbershop)
        .scalar_subquery()
    )
    return rating, reviews, barbers


def _select_with_aggregates() -> Select[tuple[Barbershop, Any, Any, Any]]:
    rating, reviews, barbers = _aggregates()
    return select(Barbershop, rating, reviews, barbers)


def _to_read(row: Any) -> BarbershopRead:
    shop, rating, reviews_count, barbers_count, *extra = row
    return BarbershopRead.model_validate(shop).model_copy(
        update={
            "rating": rating,
            "reviews_count": reviews_count,
            "barbers_count": barbers_count,
            "distance_km": round(extra[0], 2) if extra and extra[0] is not None else None,
        }
    )


async def list_shops(
    db: AsyncSession, params: PageParams, filters: ShopFilters | None = None
) -> Page[BarbershopRead]:
    """Search active shops.

    Filters compose: every one that is set narrows the same query, so
    `?city=Almaty&service=Haircut&min_rating=4&sort=-rating` is one round trip,
    not four.
    """
    criteria = filters or ShopFilters()
    rating, reviews, barbers = _aggregates()

    origin = (
        (criteria.lat, criteria.lng)
        if criteria.lat is not None and criteria.lng is not None
        else None
    )
    distance = (
        haversine_km(Barbershop.latitude, Barbershop.longitude, *origin)
        if origin is not None
        else None
    )

    conditions: list[ColumnElement[bool]] = [Barbershop.is_active.is_(True)]

    if criteria.city:
        conditions.append(func.lower(Barbershop.city) == criteria.city.strip().lower())

    if criteria.q:
        pattern = f"%{_escape_like(criteria.q.strip())}%"
        conditions.append(
            or_(
                Barbershop.name.ilike(pattern, escape="\\"),
                Barbershop.description.ilike(pattern, escape="\\"),
            )
        )

    if criteria.service:
        pattern = f"%{_escape_like(criteria.service.strip())}%"
        # EXISTS rather than a JOIN: a shop with three matching services must
        # appear once, not three times, and no DISTINCT is needed to get there.
        conditions.append(
            select(Service.id)
            .where(
                Service.shop_id == Barbershop.id,
                Service.is_active.is_(True),
                Service.name.ilike(pattern, escape="\\"),
            )
            .exists()
        )

    if criteria.min_rating is not None:
        conditions.append(rating >= criteria.min_rating)

    if criteria.radius_km is not None and origin is not None and distance is not None:
        lat_min, lat_max, lng_min, lng_max = bounding_box(*origin, criteria.radius_km)
        # Cheap box first so the index can discard most rows, then the exact
        # circle on what survives.
        conditions.extend(
            [
                Barbershop.latitude.between(lat_min, lat_max),
                Barbershop.longitude.between(lng_min, lng_max),
                distance <= float(criteria.radius_km),
            ]
        )
    elif criteria.has_origin:
        conditions.append(Barbershop.latitude.is_not(None))

    total = (
        await db.execute(select(func.count()).select_from(Barbershop).where(*conditions))
    ).scalar_one()

    columns = select(Barbershop, rating, reviews, barbers)
    if distance is not None:
        columns = columns.add_columns(distance.label("distance_km"))

    rows = (
        await db.execute(
            columns.where(*conditions)
            .order_by(*_ordering(criteria, rating, distance))
            .offset(params.offset)
            .limit(params.limit)
        )
    ).all()

    return Page.build([_to_read(row) for row in rows], total, params)


def _ordering(
    criteria: ShopFilters,
    rating: Any,
    distance: ColumnElement[float] | None,
) -> list[Any]:
    """Sort key, then a stable tiebreak.

    Paging without a total order silently repeats and skips rows between pages
    when several shops tie, so `id` always comes last.
    """
    match criteria.sort:
        case ShopSort.RATING_DESC:
            primary = [rating.desc().nulls_last()]
        case ShopSort.RATING_ASC:
            primary = [rating.asc()]
        case ShopSort.NAME_ASC:
            primary = [func.lower(Barbershop.name).asc()]
        case ShopSort.NAME_DESC:
            primary = [func.lower(Barbershop.name).desc()]
        case ShopSort.OLDEST:
            primary = [Barbershop.created_at.asc()]
        case ShopSort.DISTANCE:
            primary = [distance.asc()] if distance is not None else []
        case _:
            primary = [Barbershop.created_at.desc()]
    return [*primary, Barbershop.id]


def _escape_like(value: str) -> str:
    """Neutralize LIKE wildcards so a search for "100%" is not a search for everything."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_shop(db: AsyncSession, shop_id: uuid.UUID, *, viewer: User | None) -> BarbershopRead:
    # Runs the visibility rule first so a soft-deleted shop 404s for strangers.
    await get_shop_or_404(db, shop_id, viewer=viewer)
    row = (await db.execute(_select_with_aggregates().where(Barbershop.id == shop_id))).one()
    return _to_read(row)


async def create_shop(db: AsyncSession, owner: User, payload: BarbershopCreate) -> BarbershopRead:
    shop = Barbershop(owner_id=owner.id, **payload.model_dump())
    db.add(shop)
    await db.commit()
    await db.refresh(shop)
    return BarbershopRead.model_validate(shop)


async def update_shop(
    db: AsyncSession, user: User, shop_id: uuid.UUID, payload: BarbershopUpdate
) -> BarbershopRead:
    shop = await get_shop_or_404(db, shop_id, viewer=user)
    ensure_can_manage_shop(user, shop)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(shop, field, value)

    await db.commit()
    return await get_shop(db, shop_id, viewer=user)


async def deactivate_shop(db: AsyncSession, user: User, shop_id: uuid.UUID) -> None:
    """Soft delete. Refused while customers still hold bookings (§11.9)."""
    shop = await get_shop_or_404(db, shop_id, viewer=user)
    ensure_can_manage_shop(user, shop)

    upcoming = (
        await db.execute(
            select(func.count())
            .select_from(Appointment)
            .join(Barber, Barber.id == Appointment.barber_id)
            .where(
                Barber.shop_id == shop_id,
                Appointment.start_time >= utcnow(),
                Appointment.status.in_([AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED]),
            )
        )
    ).scalar_one()
    if upcoming:
        raise ConflictError(
            "This barbershop still has upcoming appointments",
            details={"upcoming_appointments": upcoming},
        )

    shop.is_active = False
    await db.commit()
