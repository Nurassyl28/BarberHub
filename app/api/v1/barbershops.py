"""Barbershop endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, MaybeUser, require_roles
from app.core.pagination import Page, PageParamsDep
from app.db.session import DbSession
from app.models import User, UserRole
from app.schemas.barbershop import BarbershopCreate, BarbershopRead, BarbershopUpdate
from app.schemas.search import ShopFilters
from app.services import barbershop as service

router = APIRouter(prefix="/barbershops", tags=["barbershops"])

OwnerOrAdmin = Depends(require_roles(UserRole.SHOP_OWNER))


@router.get(
    "",
    response_model=Page[BarbershopRead],
    summary="Search barbershops",
    description=(
        "All filters compose. `city` is an exact case-insensitive match, `q` "
        "searches name and description, `service` matches shops offering a "
        "service by that name, and `min_rating` uses the mean rating of the "
        "shop's rated barbers.\n\n"
        "Pass `lat` and `lng` together to get `distance_km` on every result; "
        "add `radius_km` to filter by it, or `sort=distance` to order by it."
    ),
)
async def list_barbershops(
    db: DbSession,
    params: PageParamsDep,
    filters: Annotated[ShopFilters, Depends()],
) -> Page[BarbershopRead]:
    return await service.list_shops(db, params, filters)


@router.post(
    "",
    response_model=BarbershopRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a barbershop",
)
async def create_barbershop(
    payload: BarbershopCreate,
    db: DbSession,
    owner: User = OwnerOrAdmin,
) -> BarbershopRead:
    return await service.create_shop(db, owner, payload)


@router.get(
    "/{shop_id}",
    response_model=BarbershopRead,
    summary="Get one barbershop",
    description="Soft-deleted shops are visible only to their owner and to admins.",
)
async def get_barbershop(shop_id: uuid.UUID, db: DbSession, viewer: MaybeUser) -> BarbershopRead:
    return await service.get_shop(db, shop_id, viewer=viewer)


@router.patch("/{shop_id}", response_model=BarbershopRead, summary="Update a barbershop")
async def update_barbershop(
    shop_id: uuid.UUID,
    payload: BarbershopUpdate,
    db: DbSession,
    user: CurrentUser,
) -> BarbershopRead:
    return await service.update_shop(db, user, shop_id, payload)


@router.delete(
    "/{shop_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a barbershop",
    description="Soft delete. Refused while the shop still has upcoming appointments.",
)
async def delete_barbershop(shop_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    await service.deactivate_shop(db, user, shop_id)
