"""Shop-wide closures."""

import uuid
from datetime import date as date_type
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Appointment, AppointmentStatus, Barber, ShopClosure, User
from app.schemas.closure import ClosureCreate, ClosureRead
from app.services.permissions import ensure_can_manage_shop, get_shop_or_404
from app.services.slots import shop_timezone
from app.utils.time import utcnow

_ACTIVE = (AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED)


async def list_closures(
    db: AsyncSession, shop_id: uuid.UUID, *, viewer: User | None, upcoming_only: bool = False
) -> list[ClosureRead]:
    shop = await get_shop_or_404(db, shop_id, viewer=viewer)
    stmt = select(ShopClosure).where(ShopClosure.shop_id == shop.id)
    if upcoming_only:
        today = utcnow().astimezone(shop_timezone(shop)).date()
        stmt = stmt.where(ShopClosure.end_date >= today)
    rows = (await db.execute(stmt.order_by(ShopClosure.start_date))).scalars().all()
    return [ClosureRead.model_validate(row) for row in rows]


async def create_closure(
    db: AsyncSession, user: User, shop_id: uuid.UUID, payload: ClosureCreate
) -> ClosureRead:
    shop = await get_shop_or_404(db, shop_id, viewer=user)
    ensure_can_manage_shop(user, shop)

    tz = shop_timezone(shop)
    # Local calendar days become instants only here, at the boundary — the same
    # rule the availability engine follows.
    window_start = _local_midnight(payload.start_date, tz)
    window_end = _local_midnight(payload.end_date, tz, days_after=1)

    clashing = (
        (
            await db.execute(
                select(Appointment.id)
                .join(Barber, Barber.id == Appointment.barber_id)
                .where(
                    Barber.shop_id == shop.id,
                    Appointment.status.in_(_ACTIVE),
                    Appointment.start_time < window_end,
                    Appointment.end_time > window_start,
                )
            )
        )
        .scalars()
        .all()
    )
    if clashing:
        # Closing over booked time would leave those customers holding an
        # appointment on a day the shop says it is shut.
        raise ConflictError(
            "Customers already have appointments during that period",
            details={
                "conflicting_appointments": [str(row) for row in clashing[:20]],
                "count": len(clashing),
            },
        )

    closure = ShopClosure(
        shop_id=shop.id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        reason=payload.reason,
    )
    db.add(closure)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("That closure is already recorded") from exc
    await db.refresh(closure)
    return ClosureRead.model_validate(closure)


async def delete_closure(db: AsyncSession, user: User, closure_id: uuid.UUID) -> None:
    closure = await db.get(ShopClosure, closure_id)
    if closure is None:
        raise NotFoundError("Closure not found")
    shop = await get_shop_or_404(db, closure.shop_id, viewer=user)
    ensure_can_manage_shop(user, shop)

    await db.delete(closure)
    await db.commit()


async def is_closed_on(db: AsyncSession, shop_id: uuid.UUID, day: date_type) -> bool:
    """Whether the shop is shut on one local calendar day."""
    found = (
        await db.execute(
            select(func.count())
            .select_from(ShopClosure)
            .where(
                ShopClosure.shop_id == shop_id,
                ShopClosure.start_date <= day,
                ShopClosure.end_date >= day,
            )
        )
    ).scalar_one()
    return bool(found)


def _local_midnight(day: date_type, tz: ZoneInfo, days_after: int = 0) -> datetime:
    return datetime.combine(day + timedelta(days=days_after), time.min, tzinfo=tz)
