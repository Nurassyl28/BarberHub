"""Keeping `barbers.rating` honest.

The column is denormalized so that listing and sorting shops never has to
aggregate the whole reviews table. That only stays correct if every write
recomputes it, which is what this module is for (see docs/SPEC.md §11.3).
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Barber, Review


async def lock_barber_for_update(db: AsyncSession, barber_id: uuid.UUID) -> Barber:
    """Take the barber's row lock *before* touching `reviews`.

    Order matters, and getting it wrong deadlocks. Inserting a review makes
    PostgreSQL take a `FOR KEY SHARE` lock on the referenced `barbers` row, to
    stop the foreign key being pulled out from under it. If the recomputation
    then asks for `FOR UPDATE`, that is a lock *upgrade* — and two concurrent
    reviewers, each already holding KEY SHARE, will wait on each other forever.
    Postgres notices and kills one with `DeadlockDetectedError`.

    Acquiring the exclusive lock first means every writer takes the same locks
    in the same order, so they queue instead of deadlocking.
    """
    return (
        await db.execute(select(Barber).where(Barber.id == barber_id).with_for_update())
    ).scalar_one()


async def recompute_barber_rating(db: AsyncSession, barber_id: uuid.UUID) -> Barber:
    """Recalculate one barber's rating from their reviews.

    Callers that also insert a review must call `lock_barber_for_update` first;
    see the note there. On its own — the nightly repair job, say — this is the
    only writer and the lock it takes here is enough.
    """
    barber = await lock_barber_for_update(db, barber_id)

    average, count = (
        await db.execute(
            select(func.avg(Review.rating), func.count()).where(Review.barber_id == barber_id)
        )
    ).one()

    barber.rating = (
        Decimal("0")
        if not count
        else Decimal(average).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )
    barber.reviews_count = count
    return barber
