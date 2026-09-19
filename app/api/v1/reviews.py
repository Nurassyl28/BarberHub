"""Review endpoints."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser
from app.core.pagination import Page, PageParamsDep
from app.db.session import DbSession
from app.schemas.review import BarberRatingRead, ReviewCreate, ReviewRead
from app.services import reviews as service

appointment_scoped = APIRouter(prefix="/appointments", tags=["reviews"])
barber_scoped = APIRouter(prefix="/barbers", tags=["reviews"])


@appointment_scoped.post(
    "/{appointment_id}/review",
    response_model=ReviewRead,
    status_code=status.HTTP_201_CREATED,
    summary="Review a completed appointment",
    description="The appointment's own customer, once, and only after it is marked COMPLETED.",
)
async def create_review(
    appointment_id: uuid.UUID, payload: ReviewCreate, db: DbSession, user: CurrentUser
) -> ReviewRead:
    return await service.create_review(db, user, appointment_id, payload)


@barber_scoped.get(
    "/{barber_id}/reviews",
    response_model=Page[ReviewRead],
    summary="A barber's reviews",
    description="Public. Authors appear as a first name and a last initial.",
)
async def list_reviews(
    barber_id: uuid.UUID, db: DbSession, params: PageParamsDep
) -> Page[ReviewRead]:
    return await service.list_barber_reviews(db, barber_id, params)


@barber_scoped.get(
    "/{barber_id}/rating",
    response_model=BarberRatingRead,
    summary="A barber's rating summary",
)
async def get_rating(barber_id: uuid.UUID, db: DbSession) -> BarberRatingRead:
    return await service.get_barber_rating(db, barber_id)
