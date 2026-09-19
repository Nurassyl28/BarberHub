"""Service catalogue endpoints."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, MaybeUser
from app.db.session import DbSession
from app.schemas.service import ServiceCreate, ServiceRead, ServiceUpdate
from app.services import service_catalog as catalog

shop_scoped = APIRouter(prefix="/barbershops/{shop_id}/services", tags=["services"])
router = APIRouter(prefix="/services", tags=["services"])


@shop_scoped.get(
    "",
    response_model=list[ServiceRead],
    summary="List a shop's services",
    description="Retired services are shown only to the shop owner and admins.",
)
async def list_services(shop_id: uuid.UUID, db: DbSession, viewer: MaybeUser) -> list[ServiceRead]:
    return await catalog.list_services(db, shop_id, viewer=viewer)


@shop_scoped.post(
    "",
    response_model=ServiceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a service",
)
async def create_service(
    shop_id: uuid.UUID, payload: ServiceCreate, db: DbSession, user: CurrentUser
) -> ServiceRead:
    return await catalog.create_service(db, user, shop_id, payload)


@router.patch("/{service_id}", response_model=ServiceRead, summary="Update a service")
async def update_service(
    service_id: uuid.UUID, payload: ServiceUpdate, db: DbSession, user: CurrentUser
) -> ServiceRead:
    return await catalog.update_service(db, user, service_id, payload)


@router.delete(
    "/{service_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Retire a service",
    description="Soft delete, so historical appointments keep pointing at what "
    "was actually sold. Refused while upcoming appointments use it.",
)
async def delete_service(service_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    await catalog.deactivate_service(db, user, service_id)
