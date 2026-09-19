"""Dashboard aggregates.

Written as a handful of grouped queries rather than a loop over appointments:
the counts, the revenue, the popular service and the top barber are each one
round trip, and none of them grows with the number of rows (see docs/SPEC.md §8).
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.models import Appointment, AppointmentStatus, Barber, Barbershop, Service, User, UserRole
from app.schemas.dashboard import (
    BarberDashboard,
    Period,
    PopularService,
    ShopDashboard,
    TopBarber,
)
from app.services.permissions import get_shop_or_404
from app.services.slots import shop_timezone
from app.utils.time import utcnow

#: Only completed work counts as revenue. A booking that was cancelled or
#: no-showed produced no money, however much it was going to be worth.
_EARNING = AppointmentStatus.COMPLETED


async def shop_dashboard(
    db: AsyncSession,
    user: User,
    shop_id: uuid.UUID,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    now: datetime | None = None,
) -> ShopDashboard:
    shop = await get_shop_or_404(db, shop_id, viewer=user)
    if user.role is not UserRole.ADMIN and shop.owner_id != user.id:
        raise ForbiddenError("You do not own this barbershop")

    tz = shop_timezone(shop)
    moment = now or utcnow()
    period = _resolve_period(date_from, date_to, tz, moment)
    scope = Barber.shop_id == shop_id

    counts = await _status_counts(db, scope, period)
    revenue, average = await _revenue(db, scope, period)

    return ShopDashboard(
        shop_id=shop_id,
        period=Period(date_from=period[0], date_to=period[1], timezone=str(tz)),
        today=moment.astimezone(tz).date(),
        total_appointments=sum(counts.values()),
        today_appointments=await _today_count(db, scope, tz, moment),
        completed_appointments=counts.get(AppointmentStatus.COMPLETED, 0),
        cancelled_appointments=counts.get(AppointmentStatus.CANCELLED, 0),
        no_show_appointments=counts.get(AppointmentStatus.NO_SHOW, 0),
        monthly_revenue=revenue,
        average_ticket=average,
        most_popular_service=await _popular_service(db, scope, period),
        top_barber=await _top_barber(db, shop_id, period),
    )


async def barber_dashboard(
    db: AsyncSession,
    user: User,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    now: datetime | None = None,
) -> BarberDashboard:
    barber = (
        await db.execute(select(Barber).where(Barber.user_id == user.id).limit(1))
    ).scalar_one_or_none()
    if barber is None:
        raise NotFoundError("You do not have a barber profile")

    shop = await db.get(Barbershop, barber.shop_id)
    tz = shop_timezone(shop) if shop else ZoneInfo("UTC")
    moment = now or utcnow()
    period = _resolve_period(date_from, date_to, tz, moment)
    scope = Appointment.barber_id == barber.id

    counts = await _status_counts(db, scope, period)
    revenue, average = await _revenue(db, scope, period)

    return BarberDashboard(
        barber_id=barber.id,
        period=Period(date_from=period[0], date_to=period[1], timezone=str(tz)),
        today=moment.astimezone(tz).date(),
        total_appointments=sum(counts.values()),
        today_appointments=await _today_count(db, scope, tz, moment),
        completed_appointments=counts.get(AppointmentStatus.COMPLETED, 0),
        cancelled_appointments=counts.get(AppointmentStatus.CANCELLED, 0),
        no_show_appointments=counts.get(AppointmentStatus.NO_SHOW, 0),
        revenue=revenue,
        average_ticket=average,
        rating=barber.rating,
        reviews_count=barber.reviews_count,
        most_popular_service=await _popular_service(db, scope, period),
    )


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _resolve_period(
    date_from: datetime | None,
    date_to: datetime | None,
    tz: ZoneInfo,
    now: datetime,
) -> tuple[datetime, datetime]:
    """Default to the current calendar month *in the shop's own timezone*.

    A shop in Almaty closing its books on the 31st means 31st local, not 31st
    UTC — which is five hours earlier and would push a whole evening of takings
    into the next month.
    """
    if date_from is not None and date_to is not None:
        return date_from, date_to

    local = now.astimezone(tz)
    month_start = datetime(local.year, local.month, 1, tzinfo=tz)
    next_month = datetime(
        local.year + (local.month == 12),
        1 if local.month == 12 else local.month + 1,
        1,
        tzinfo=tz,
    )
    return date_from or month_start, date_to or next_month


async def _status_counts(
    db: AsyncSession, scope: ColumnElement[bool], period: tuple[datetime, datetime]
) -> dict[AppointmentStatus, int]:
    """One grouped query instead of five `COUNT(*)` round trips."""
    rows = (
        await db.execute(
            select(Appointment.status, func.count())
            .select_from(Appointment)
            .join(Barber, Barber.id == Appointment.barber_id)
            .where(scope, Appointment.start_time >= period[0], Appointment.start_time < period[1])
            .group_by(Appointment.status)
        )
    ).all()
    return dict(rows)  # type: ignore[arg-type]


async def _revenue(
    db: AsyncSession, scope: ColumnElement[bool], period: tuple[datetime, datetime]
) -> tuple[Decimal, Decimal]:
    total, count = (
        await db.execute(
            select(func.coalesce(func.sum(Appointment.total_price), 0), func.count())
            .select_from(Appointment)
            .join(Barber, Barber.id == Appointment.barber_id)
            .where(
                scope,
                Appointment.status == _EARNING,
                Appointment.start_time >= period[0],
                Appointment.start_time < period[1],
            )
        )
    ).one()
    revenue = Decimal(total)
    average = (revenue / count).quantize(Decimal("0.01")) if count else Decimal("0.00")
    return revenue.quantize(Decimal("0.01")), average


async def _today_count(
    db: AsyncSession, scope: ColumnElement[bool], tz: ZoneInfo, now: datetime
) -> int:
    """ "Today" is the shop's calendar day, not the server's."""
    local_today = now.astimezone(tz).date()
    start = datetime.combine(local_today, datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=1)
    return (
        await db.execute(
            select(func.count())
            .select_from(Appointment)
            .join(Barber, Barber.id == Appointment.barber_id)
            .where(scope, Appointment.start_time >= start, Appointment.start_time < end)
        )
    ).scalar_one()


async def _popular_service(
    db: AsyncSession, scope: ColumnElement[bool], period: tuple[datetime, datetime]
) -> PopularService | None:
    row = (
        await db.execute(
            select(Service.id, Service.name, func.count().label("bookings"))
            .select_from(Appointment)
            .join(Barber, Barber.id == Appointment.barber_id)
            .join(Service, Service.id == Appointment.service_id)
            .where(
                scope,
                Appointment.status != AppointmentStatus.CANCELLED,
                Appointment.start_time >= period[0],
                Appointment.start_time < period[1],
            )
            .group_by(Service.id, Service.name)
            # `Service.name` breaks ties so the answer is stable between calls
            # rather than whichever row the planner happened to emit first.
            .order_by(func.count().desc(), Service.name)
            .limit(1)
        )
    ).first()
    return None if row is None else PopularService(id=row[0], name=row[1], count=row[2])


async def _top_barber(
    db: AsyncSession, shop_id: uuid.UUID, period: tuple[datetime, datetime]
) -> TopBarber | None:
    row = (
        await db.execute(
            select(
                Barber.id,
                User.first_name,
                User.last_name,
                func.count().label("completed"),
                func.coalesce(func.sum(Appointment.total_price), 0).label("revenue"),
            )
            .select_from(Appointment)
            .join(Barber, Barber.id == Appointment.barber_id)
            .join(User, User.id == Barber.user_id)
            .where(
                Barber.shop_id == shop_id,
                Appointment.status == _EARNING,
                Appointment.start_time >= period[0],
                Appointment.start_time < period[1],
            )
            .group_by(Barber.id, User.first_name, User.last_name)
            .order_by(func.coalesce(func.sum(Appointment.total_price), 0).desc(), Barber.id)
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return TopBarber(
        id=row[0],
        name=f"{row[1]} {row[2]}",
        completed=row[3],
        revenue=Decimal(row[4]).quantize(Decimal("0.01")),
    )
