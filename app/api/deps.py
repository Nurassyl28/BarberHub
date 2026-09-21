"""Request dependencies: who is calling, and may they do this."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.db.session import DbSession
from app.models import User, UserRole

# auto_error=False so a missing header reaches our handler and comes back in the
# standard error envelope rather than FastAPI's own `{"detail": ...}` shape.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


async def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is None:
        raise UnauthorizedError("Authentication required")

    payload = decode_access_token(credentials.credentials)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("Could not validate credentials") from exc

    user = await db.get(User, user_id)
    if user is None:
        raise UnauthorizedError("Could not validate credentials")
    if not user.is_active:
        raise UnauthorizedError("This account has been deactivated")

    # The role is re-read from the database rather than trusted from the token:
    # a demotion must take effect immediately, not when the access token expires.
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_user_optional(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User | None:
    """Identify the caller on public routes, without requiring a token.

    Lets a public endpoint show a little more to the resource's owner — an
    owner can still see their own soft-deleted shop — while anonymous callers
    get the public view instead of a 401.
    """
    if credentials is None:
        return None
    return await get_current_user(db, credentials)


MaybeUser = Annotated[User | None, Depends(get_current_user_optional)]


def require_roles(*roles: UserRole) -> Callable[[User], Awaitable[User]]:
    """Dependency factory gating a route on role alone.

    Role is only ever half the check — anything scoped to a specific shop,
    barber, or appointment must also pass an ownership check from
    `app.services.permissions`.
    """
    allowed = frozenset(roles)

    async def dependency(user: CurrentUser) -> User:
        if user.role not in allowed and user.role is not UserRole.ADMIN:
            raise ForbiddenError(
                "This action requires a different role",
                details={"required": sorted(r.value for r in allowed), "actual": user.role.value},
            )
        return user

    return dependency


#: Admins are implicitly allowed everywhere `require_roles` is used, so these
#: read as "at least".
CustomerUser = Annotated[User, Depends(require_roles(UserRole.CUSTOMER))]
BarberUser = Annotated[User, Depends(require_roles(UserRole.BARBER))]
ShopOwnerUser = Annotated[User, Depends(require_roles(UserRole.SHOP_OWNER))]
AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]

__all__ = [
    "AdminUser",
    "AsyncSession",
    "BarberUser",
    "CurrentUser",
    "CustomerUser",
    "DbSession",
    "MaybeUser",
    "ShopOwnerUser",
    "get_current_user",
    "get_current_user_optional",
    "require_roles",
]
