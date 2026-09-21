"""Shop closure endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, MaybeUser
from app.db.session import DbSession
from app.schemas.closure import ClosureCreate, ClosureRead
from app.services import closures as service

shop_scoped = APIRouter(prefix="/barbershops/{shop_id}/closures", tags=["closures"])
router = APIRouter(prefix="/closures", tags=["closures"])


@shop_scoped.get(
    "",
    response_model=list[ClosureRead],
    summary="Days the shop is shut",
    description="Public: a customer should be able to see the shop is closed "
    "over the New Year without guessing from empty slot lists.",
)
async def list_closures(
    shop_id: uuid.UUID,
    db: DbSession,
    viewer: MaybeUser,
    upcoming_only: Annotated[bool, Query(description="Hide closures already past")] = False,
) -> list[ClosureRead]:
    return await service.list_closures(db, shop_id, viewer=viewer, upcoming_only=upcoming_only)


@shop_scoped.post(
    "",
    response_model=ClosureRead,
    status_code=status.HTTP_201_CREATED,
    summary="Close the shop for a period",
    description="Dates are the shop's local calendar days and `end_date` is "
    "inclusive. Refused if customers already hold appointments in that period.",
)
async def create_closure(
    shop_id: uuid.UUID, payload: ClosureCreate, db: DbSession, user: CurrentUser
) -> ClosureRead:
    return await service.create_closure(db, user, shop_id, payload)


@router.delete(
    "/{closure_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reopen a closed period",
)
async def delete_closure(closure_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    await service.delete_closure(db, user, closure_id)
