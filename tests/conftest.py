"""Shared fixtures.

Every test runs inside a transaction that is rolled back afterwards, so tests
share one migrated database without sharing state.
"""

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import time
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token, hash_password
from app.db.session import engine, get_db
from app.main import app
from app.models import (
    Barber,
    Barbershop,
    Service,
    User,
    UserRole,
    WorkingHours,
    barber_services,
)


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession]:
    """A session bound to a transaction that is always rolled back.

    The outer transaction never commits, so `session.commit()` inside a test
    only releases a nested SAVEPOINT — the database is untouched afterwards.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as session:
            yield session
        await transaction.rollback()


@pytest.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """HTTP client bound to the app in-process — no network, no running server.

    `get_db` is overridden to hand routes the same rolled-back session the test
    uses, so requests made here are visible to the test and vanish afterwards.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        yield db

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


def unique_city() -> str:
    """A city name no other row in the database uses."""
    return f"Testville{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def shop_fixture(db: AsyncSession) -> dict[str, object]:
    """A minimal but complete shop: owner, shop, barber, customer, service."""
    owner = User(
        first_name="Olzhas",
        last_name="Owner",
        email=unique_email("owner"),
        password_hash="not-a-real-hash",
        role=UserRole.SHOP_OWNER,
    )
    barber_user = User(
        first_name="Aidar",
        last_name="Barber",
        email=unique_email("barber"),
        password_hash="not-a-real-hash",
        role=UserRole.BARBER,
    )
    customer = User(
        first_name="Dana",
        last_name="Customer",
        email=unique_email("customer"),
        password_hash="not-a-real-hash",
    )
    db.add_all([owner, barber_user, customer])
    await db.flush()

    shop = Barbershop(
        owner_id=owner.id,
        name="Sharp Cuts",
        address="Abay 10",
        city="Almaty",
        timezone="Asia/Almaty",
    )
    db.add(shop)
    await db.flush()

    barber = Barber(user_id=barber_user.id, shop_id=shop.id, experience_years=5)
    service = Service(
        shop_id=shop.id,
        name="Haircut",
        duration_minutes=45,
        price=Decimal("5000.00"),
    )
    db.add_all([barber, service])
    await db.flush()

    return {
        "owner": owner,
        "barber_user": barber_user,
        "customer": customer,
        "shop": shop,
        "barber": barber,
        "service": service,
    }


# --------------------------------------------------------------------------- #
# auth helpers
# --------------------------------------------------------------------------- #

REGISTER_PASSWORD = "Sup3rSecret!"


@pytest.fixture
def register_payload() -> Callable[..., dict[str, str]]:
    def build(**overrides: str) -> dict[str, str]:
        return {
            "first_name": "Dana",
            "last_name": "Test",
            "email": unique_email(),
            "password": REGISTER_PASSWORD,
        } | overrides

    return build


@pytest.fixture
async def make_user(db: AsyncSession) -> Callable[..., Awaitable[User]]:
    """Insert a user with a real argon2 hash of `REGISTER_PASSWORD`."""

    async def build(role: UserRole = UserRole.CUSTOMER, **overrides: Any) -> User:
        user = User(
            first_name=overrides.pop("first_name", "Test"),
            last_name=overrides.pop("last_name", "User"),
            email=overrides.pop("email", unique_email(role.value.lower())),
            password_hash=hash_password(REGISTER_PASSWORD),
            role=role,
            **overrides,
        )
        db.add(user)
        await db.flush()
        return user

    return build


@pytest.fixture
def auth_headers() -> Callable[[User], dict[str, str]]:
    """Authorization header for a user, without going through /auth/login."""

    def build(user: User) -> dict[str, str]:
        token = create_access_token(user.id, user.role.value)
        return {"Authorization": f"Bearer {token}"}

    return build


@pytest.fixture
async def make_shop(db: AsyncSession) -> Callable[..., Awaitable[Barbershop]]:
    """Insert a shop owned by the given user."""

    async def build(owner: User, **overrides: Any) -> Barbershop:
        shop = Barbershop(
            owner_id=owner.id,
            name=overrides.pop("name", "Sharp Cuts"),
            address=overrides.pop("address", "Abay 10"),
            # A city of its own unless the test says otherwise: the database is
            # shared and long-lived, so "every shop in Almaty" is not a
            # statement about this test's data.
            city=overrides.pop("city", unique_city()),
            **overrides,
        )
        db.add(shop)
        await db.flush()
        return shop

    return build


@pytest.fixture
async def make_barber(db: AsyncSession) -> Callable[..., Awaitable[Barber]]:
    async def build(user: User, shop: Barbershop, **overrides: Any) -> Barber:
        barber = Barber(user_id=user.id, shop_id=shop.id, **overrides)
        db.add(barber)
        await db.flush()
        return barber

    return build


def shop_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Sharp Cuts",
        "address": "Abay 10",
        "city": "Almaty",
    } | overrides


@pytest.fixture
async def bookable(
    db: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_shop: Callable[..., Awaitable[Barbershop]],
    make_barber: Callable[..., Awaitable[Barber]],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """A shop that can actually take bookings: barber, service, working hours."""

    async def build(
        timezone: str = "Asia/Almaty",
        duration_minutes: int = 45,
        weekday: int | None = None,
        start: time = time(9),
        end: time = time(18),
    ) -> dict[str, Any]:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner, timezone=timezone)
        barber_user = await make_user(role=UserRole.BARBER)
        barber = await make_barber(barber_user, shop)
        service = Service(
            shop_id=shop.id,
            name=f"Haircut {uuid.uuid4().hex[:6]}",
            duration_minutes=duration_minutes,
            price=Decimal("5000.00"),
        )
        db.add(service)
        await db.flush()
        await db.execute(
            barber_services.insert().values(barber_id=barber.id, service_id=service.id)
        )
        for day in range(7) if weekday is None else [weekday]:
            db.add(WorkingHours(barber_id=barber.id, weekday=day, start_time=start, end_time=end))
        await db.flush()
        return {
            "owner": owner,
            "shop": shop,
            "barber": barber,
            "barber_user": barber_user,
            "service": service,
            "customer": await make_user(role=UserRole.CUSTOMER),
        }

    return build


@pytest.fixture
def city() -> str:
    """One unique city per test, to scope search assertions to its own rows."""
    return unique_city()
