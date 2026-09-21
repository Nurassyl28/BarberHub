"""Barber endpoints."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, MaybeUser
from app.db.session import DbSession
from app.schemas.barber import BarberCreate, BarberRead, BarberServicesUpdate, BarberUpdate
from app.services import barber_directory as service

# Two routers: barbers are listed under their shop but addressed on their own.
shop_scoped = APIRouter(prefix="/barbershops/{shop_id}/barbers", tags=["barbers"])
router = APIRouter(prefix="/barbers", tags=["barbers"])


@shop_scoped.get("", response_model=list[BarberRead], summary="List a shop's barbers")
async def list_barbers(shop_id: uuid.UUID, db: DbSession, viewer: MaybeUser) -> list[BarberRead]:
    return await service.list_barbers(db, shop_id, viewer=viewer)


@shop_scoped.post(
    "",
    response_model=BarberRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a barber to a shop",
    description="The person must already have an account. A CUSTOMER account is "
    "promoted to BARBER; higher roles are left unchanged.",
)
async def add_barber(
    shop_id: uuid.UUID, payload: BarberCreate, db: DbSession, user: CurrentUser
) -> BarberRead:
    return await service.add_barber(db, user, shop_id, payload)


@router.get("/{barber_id}", response_model=BarberRead, summary="Get one barber")
async def get_barber(barber_id: uuid.UUID, db: DbSession) -> BarberRead:
    return await service.get_barber(db, barber_id)


@router.patch(
    "/{barber_id}",
    response_model=BarberRead,
    summary="Update a barber profile",
    description="The barber themselves, the shop owner, or an admin.",
)
async def update_barber(
    barber_id: uuid.UUID, payload: BarberUpdate, db: DbSession, user: CurrentUser
) -> BarberRead:
    return await service.update_barber(db, user, barber_id, payload)


@router.delete(
    "/{barber_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a barber from a shop",
    description="Soft delete, shop owner only. Refused while the barber has upcoming appointments.",
)
async def delete_barber(barber_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    await service.deactivate_barber(db, user, barber_id)


@router.put(
    "/{barber_id}/services",
    response_model=BarberRead,
    summary="Set which services this barber performs",
    description="Full replacement. Every id must be an active service of the barber's own shop.",
)
async def set_barber_services(
    barber_id: uuid.UUID, payload: BarberServicesUpdate, db: DbSession, user: CurrentUser
) -> BarberRead:
    return await service.set_services(db, user, barber_id, payload)
