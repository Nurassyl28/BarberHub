"""`require_roles` in isolation — no HTTP, no database."""

import uuid

import pytest

from app.api.deps import require_roles
from app.core.errors import ForbiddenError
from app.models import User, UserRole


def make_user(role: UserRole) -> User:
    return User(
        id=uuid.uuid4(),
        first_name="A",
        last_name="B",
        email="a@b.kz",
        password_hash="x",
        role=role,
    )


async def test_matching_role_passes() -> None:
    guard = require_roles(UserRole.SHOP_OWNER)
    user = make_user(UserRole.SHOP_OWNER)

    assert await guard(user) is user


async def test_wrong_role_is_forbidden() -> None:
    guard = require_roles(UserRole.SHOP_OWNER)

    with pytest.raises(ForbiddenError) as excinfo:
        await guard(make_user(UserRole.CUSTOMER))

    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "FORBIDDEN"
    assert excinfo.value.details == {"required": ["SHOP_OWNER"], "actual": "CUSTOMER"}


async def test_admin_passes_every_gate() -> None:
    """Admins are implicitly allowed wherever a role gate is used (§2)."""
    for role in (UserRole.CUSTOMER, UserRole.BARBER, UserRole.SHOP_OWNER):
        guard = require_roles(role)

        assert await guard(make_user(UserRole.ADMIN)) is not None


async def test_any_of_several_roles_passes() -> None:
    guard = require_roles(UserRole.BARBER, UserRole.SHOP_OWNER)

    assert await guard(make_user(UserRole.BARBER)) is not None
    assert await guard(make_user(UserRole.SHOP_OWNER)) is not None
    with pytest.raises(ForbiddenError):
        await guard(make_user(UserRole.CUSTOMER))
