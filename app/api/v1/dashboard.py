"""Dashboard endpoints."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser
from app.db.session import DbSession
from app.schemas.dashboard import BarberDashboard, ShopDashboard
from app.services import analytics

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

DateFrom = Annotated[datetime | None, Query(description="Defaults to the shop's current month")]
DateTo = Annotated[datetime | None, Query(description="Exclusive upper bound")]


@router.get(
    "/barbershop/{shop_id}",
    response_model=ShopDashboard,
    summary="Shop statistics",
    description="Shop owner or admin. Without a date range this reports the "
    "current calendar month in the shop's own timezone. Revenue counts "
    "COMPLETED appointments only.",
)
async def shop_dashboard(
    shop_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> ShopDashboard:
    return await analytics.shop_dashboard(db, user, shop_id, date_from=date_from, date_to=date_to)


@router.get(
    "/barber/me",
    response_model=BarberDashboard,
    summary="The calling barber's own statistics",
)
async def barber_dashboard(
    db: DbSession,
    user: CurrentUser,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> BarberDashboard:
    return await analytics.barber_dashboard(db, user, date_from=date_from, date_to=date_to)
