"""Ownership checks.

Role alone never authorizes anything scoped to a resource: a SHOP_OWNER may
edit *their* shop, not any shop. Every guard here answers "is this user related
to this row", and routes combine it with the role gate from `app.api.deps`
(see docs/SPEC.md §2).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.models import Barber, Barbershop, User, UserRole


async def get_shop_or_404(
    db: AsyncSession,
    shop_id: uuid.UUID,
    *,
    viewer: User | None = None,
) -> Barbershop:
    """Load a shop, hiding soft-deleted ones from everyone but their owner."""
    shop = await db.get(Barbershop, shop_id)
    if shop is None:
        raise NotFoundError("Barbershop not found")
    if not shop.is_active and not _can_manage_shop(viewer, shop):
        raise NotFoundError("Barbershop not found")
    return shop


def ensure_can_manage_shop(user: User, shop: Barbershop) -> None:
    if not _can_manage_shop(user, shop):
        raise ForbiddenError("You do not own this barbershop")


async def ensure_can_manage_barber(db: AsyncSession, user: User, barber: Barber) -> None:
    """A barber's own row, or any barber in a shop the user owns."""
    if user.role is UserRole.ADMIN or barber.user_id == user.id:
        return
    shop = await db.get(Barbershop, barber.shop_id)
    if shop is None or shop.owner_id != user.id:
        raise ForbiddenError("You do not manage this barber")


async def owned_shop_ids(db: AsyncSession, user: User) -> list[uuid.UUID]:
    result = await db.execute(select(Barbershop.id).where(Barbershop.owner_id == user.id))
    return list(result.scalars().all())


def _can_manage_shop(user: User | None, shop: Barbershop) -> bool:
    return user is not None and (user.role is UserRole.ADMIN or shop.owner_id == user.id)
