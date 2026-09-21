"""Working hours and break endpoints."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser
from app.db.session import DbSession
from app.schemas.schedule import BreakCreate, BreakRead, WorkingHoursRead, WorkingHoursUpdate
from app.services import schedule as service

router = APIRouter(prefix="/barbers", tags=["schedule"])
breaks_router = APIRouter(prefix="/breaks", tags=["schedule"])


@router.get(
    "/{barber_id}/working-hours",
    response_model=list[WorkingHoursRead],
    summary="A barber's weekly schedule",
    description="Times are the shop's local wall clock. Weekday 0 is Monday.",
)
async def get_working_hours(barber_id: uuid.UUID, db: DbSession) -> list[WorkingHoursRead]:
    return await service.list_working_hours(db, barber_id)


@router.put(
    "/{barber_id}/working-hours",
    response_model=list[WorkingHoursRead],
    summary="Replace a barber's weekly schedule",
    description="Full replacement: whatever is sent becomes the whole week. "
    "Several shifts per weekday are allowed (split shifts), as long as they do "
    "not overlap. Existing appointments are not affected.",
)
async def put_working_hours(
    barber_id: uuid.UUID, payload: WorkingHoursUpdate, db: DbSession, user: CurrentUser
) -> list[WorkingHoursRead]:
    return await service.replace_working_hours(db, user, barber_id, payload)


@router.get(
    "/{barber_id}/breaks",
    response_model=list[BreakRead],
    summary="A barber's breaks",
    description="Staff only — a customer sees the effect of breaks in the free "
    "slots, not the reasons behind them.",
)
async def get_breaks(
    barber_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
) -> list[BreakRead]:
    return await service.list_breaks(db, user, barber_id, date_from=date_from, date_to=date_to)


@router.post(
    "/{barber_id}/breaks",
    response_model=BreakRead,
    status_code=status.HTTP_201_CREATED,
    summary="Block out time",
    description="Refused when it would cover an already-booked appointment.",
)
async def create_break(
    barber_id: uuid.UUID, payload: BreakCreate, db: DbSession, user: CurrentUser
) -> BreakRead:
    return await service.create_break(db, user, barber_id, payload)


@breaks_router.delete(
    "/{break_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a break",
)
async def delete_break(break_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    await service.delete_break(db, user, break_id)
