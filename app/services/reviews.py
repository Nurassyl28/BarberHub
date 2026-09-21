"""Writing and reading reviews."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import (
    AlreadyReviewedError,
    ForbiddenError,
    InvalidTransitionError,
    NotFoundError,
)
from app.core.pagination import Page, PageParams
from app.models import Appointment, AppointmentStatus, Barber, Review, User
from app.schemas.review import BarberRatingRead, ReviewCreate, ReviewRead
from app.services.ratings import lock_barber_for_update, recompute_barber_rating


async def create_review(
    db: AsyncSession, user: User, appointment_id: uuid.UUID, payload: ReviewCreate
) -> ReviewRead:
    """Review a finished haircut.

    The review and the barber's recomputed rating are written in one
    transaction: a crash between them would leave a rating that disagrees with
    the reviews it claims to summarize.
    """
    appointment = await db.get(Appointment, appointment_id)
    if appointment is None:
        raise NotFoundError("Appointment not found")
    if appointment.customer_id != user.id:
        # Deliberately not 404: the customer knows their own appointment exists,
        # and a stranger guessing UUIDs learns nothing either way.
        raise ForbiddenError("You can only review your own appointments")
    if appointment.status is not AppointmentStatus.COMPLETED:
        raise InvalidTransitionError(
            "Only completed appointments can be reviewed",
            details={"status": appointment.status.value},
        )

    # Before the INSERT, not after: the insert itself takes a shared lock on
    # this barber via the foreign key, and upgrading that to exclusive
    # afterwards deadlocks two concurrent reviewers (see `ratings`).
    await lock_barber_for_update(db, appointment.barber_id)

    review = Review(
        customer_id=user.id,
        barber_id=appointment.barber_id,
        appointment_id=appointment.id,
        rating=payload.rating,
        comment=payload.comment,
    )
    db.add(review)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise AlreadyReviewedError() from exc

    await recompute_barber_rating(db, appointment.barber_id)
    await db.commit()

    return await _load(db, review.id)


async def list_barber_reviews(
    db: AsyncSession, barber_id: uuid.UUID, params: PageParams
) -> Page[ReviewRead]:
    barber = await db.get(Barber, barber_id)
    if barber is None:
        raise NotFoundError("Barber not found")

    total = (
        await db.execute(
            select(func.count()).select_from(Review).where(Review.barber_id == barber_id)
        )
    ).scalar_one()
    rows = (
        (
            await db.execute(
                select(Review)
                .where(Review.barber_id == barber_id)
                .options(selectinload(Review.customer))
                .order_by(Review.created_at.desc(), Review.id)
                .offset(params.offset)
                .limit(params.limit)
            )
        )
        .scalars()
        .all()
    )
    return Page.build([_to_read(row) for row in rows], total, params)


async def get_barber_rating(db: AsyncSession, barber_id: uuid.UUID) -> BarberRatingRead:
    barber = await db.get(Barber, barber_id)
    if barber is None:
        raise NotFoundError("Barber not found")
    return BarberRatingRead(
        barber_id=barber.id, rating=barber.rating, reviews_count=barber.reviews_count
    )


async def _load(db: AsyncSession, review_id: uuid.UUID) -> ReviewRead:
    review = (
        await db.execute(
            select(Review).where(Review.id == review_id).options(selectinload(Review.customer))
        )
    ).scalar_one()
    return _to_read(review)


def _to_read(review: Review) -> ReviewRead:
    return ReviewRead(
        id=review.id,
        barber_id=review.barber_id,
        appointment_id=review.appointment_id,
        rating=review.rating,
        comment=review.comment,
        author=_display_name(review.customer),
        created_at=review.created_at,
    )


def _display_name(customer: User) -> str:
    initial = f" {customer.last_name[0]}." if customer.last_name else ""
    return f"{customer.first_name}{initial}"
