"""Free-slot endpoint."""

import uuid
from datetime import date as date_type
from typing import Annotated

from fastapi import APIRouter, Query

from app.db.session import DbSession
from app.schemas.availability import AvailabilityRead
from app.services import slots as service

router = APIRouter(prefix="/barbers", tags=["availability"])


@router.get(
    "/{barber_id}/available-slots",
    response_model=AvailabilityRead,
    summary="Free slots for one barber on one day",
    description=(
        "`service_id` is required: slot width is the service's duration, so "
        "availability cannot be computed without it.\n\n"
        "`date` is a calendar day in the *shop's* timezone. Slot starts come "
        "back as UTC instants plus the shop-local time to display."
    ),
)
async def available_slots(
    barber_id: uuid.UUID,
    db: DbSession,
    date: Annotated[date_type, Query(description="Calendar day, shop-local, YYYY-MM-DD")],
    service_id: Annotated[uuid.UUID, Query(description="Which service is being booked")],
) -> AvailabilityRead:
    return await service.available_slots(db, barber_id, day=date, service_id=service_id)
