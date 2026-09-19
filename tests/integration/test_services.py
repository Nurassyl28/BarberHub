"""Service catalogue: CRUD, ownership, soft delete (docs/SPEC.md §6)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, Barber, Barbershop, User, UserRole
from app.utils.time import utcnow

UserFactory = Callable[..., Awaitable[User]]
ShopFactory = Callable[..., Awaitable[Barbershop]]
BarberFactory = Callable[..., Awaitable[Barber]]
HeaderFactory = Callable[[User], dict[str, str]]

HAIRCUT = {"name": "Haircut", "duration_minutes": 45, "price": "5000.00"}


def shop_services(shop_id: uuid.UUID) -> str:
    return f"/api/v1/barbershops/{shop_id}/services"


class TestCreate:
    async def test_owner_can_create(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)

        response = await client.post(
            shop_services(shop.id), json=HAIRCUT, headers=auth_headers(owner)
        )

        assert response.status_code == 201
        body = response.json()
        assert body["shop_id"] == str(shop.id)
        assert body["is_active"] is True
        assert Decimal(body["price"]) == Decimal("5000.00")

    async def test_another_owner_cannot_create(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        intruder = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)

        response = await client.post(
            shop_services(shop.id), json=HAIRCUT, headers=auth_headers(intruder)
        )

        assert response.status_code == 403

    async def test_duplicate_name_conflicts_case_insensitively(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        headers = auth_headers(owner)
        await client.post(shop_services(shop.id), json=HAIRCUT, headers=headers)

        response = await client.post(
            shop_services(shop.id), json=HAIRCUT | {"name": "haircut"}, headers=headers
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"

    async def test_same_name_is_fine_in_a_different_shop(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        first = await make_shop(owner, name="One")
        second = await make_shop(owner, name="Two")
        headers = auth_headers(owner)
        await client.post(shop_services(first.id), json=HAIRCUT, headers=headers)

        response = await client.post(shop_services(second.id), json=HAIRCUT, headers=headers)

        assert response.status_code == 201

    async def test_zero_duration_is_rejected(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)

        response = await client.post(
            shop_services(shop.id),
            json=HAIRCUT | {"duration_minutes": 0},
            headers=auth_headers(owner),
        )

        assert response.status_code == 422

    async def test_absurd_duration_is_rejected(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)

        response = await client.post(
            shop_services(shop.id),
            json=HAIRCUT | {"duration_minutes": 1000},
            headers=auth_headers(owner),
        )

        assert response.status_code == 422

    async def test_negative_price_is_rejected(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)

        response = await client.post(
            shop_services(shop.id), json=HAIRCUT | {"price": "-1"}, headers=auth_headers(owner)
        )

        assert response.status_code == 422


class TestList:
    async def test_public_sees_only_active_services(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        headers = auth_headers(owner)
        live = (await client.post(shop_services(shop.id), json=HAIRCUT, headers=headers)).json()
        retired = (
            await client.post(
                shop_services(shop.id), json=HAIRCUT | {"name": "Old"}, headers=headers
            )
        ).json()
        await client.delete(f"/api/v1/services/{retired['id']}", headers=headers)

        public = (await client.get(shop_services(shop.id))).json()

        assert [s["id"] for s in public] == [live["id"]]

    async def test_owner_sees_retired_services_too(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        headers = auth_headers(owner)
        created = (await client.post(shop_services(shop.id), json=HAIRCUT, headers=headers)).json()
        await client.delete(f"/api/v1/services/{created['id']}", headers=headers)

        listed = (await client.get(shop_services(shop.id), headers=headers)).json()

        assert [s["id"] for s in listed] == [created["id"]]
        assert listed[0]["is_active"] is False

    async def test_unknown_shop_is_404(self, client: AsyncClient) -> None:
        response = await client.get(shop_services(uuid.uuid4()))

        assert response.status_code == 404


class TestUpdate:
    async def test_owner_can_change_price(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        headers = auth_headers(owner)
        created = (await client.post(shop_services(shop.id), json=HAIRCUT, headers=headers)).json()

        response = await client.patch(
            f"/api/v1/services/{created['id']}", json={"price": "6000.00"}, headers=headers
        )

        assert response.status_code == 200
        assert Decimal(response.json()["price"]) == Decimal("6000.00")

    async def test_another_owner_cannot_update(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        intruder = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        created = (
            await client.post(shop_services(shop.id), json=HAIRCUT, headers=auth_headers(owner))
        ).json()

        response = await client.patch(
            f"/api/v1/services/{created['id']}",
            json={"price": "1.00"},
            headers=auth_headers(intruder),
        )

        assert response.status_code == 403

    async def test_renaming_onto_an_existing_name_conflicts(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        headers = auth_headers(owner)
        await client.post(shop_services(shop.id), json=HAIRCUT, headers=headers)
        other = (
            await client.post(
                shop_services(shop.id), json=HAIRCUT | {"name": "Beard"}, headers=headers
            )
        ).json()

        response = await client.patch(
            f"/api/v1/services/{other['id']}", json={"name": "Haircut"}, headers=headers
        )

        assert response.status_code == 409

    async def test_unknown_service_is_404(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)

        response = await client.patch(
            f"/api/v1/services/{uuid.uuid4()}",
            json={"price": "1.00"},
            headers=auth_headers(owner),
        )

        assert response.status_code == 404


class TestSoftDelete:
    async def test_retires_the_service(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        headers = auth_headers(owner)
        created = (await client.post(shop_services(shop.id), json=HAIRCUT, headers=headers)).json()

        response = await client.delete(f"/api/v1/services/{created['id']}", headers=headers)

        assert response.status_code == 204

    async def test_refused_while_upcoming_appointments_use_it(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        headers = auth_headers(owner)
        created = (await client.post(shop_services(shop.id), json=HAIRCUT, headers=headers)).json()
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        customer = await make_user(role=UserRole.CUSTOMER)
        start = utcnow() + timedelta(days=1)
        db.add(
            Appointment(
                customer_id=customer.id,
                barber_id=barber.id,
                service_id=uuid.UUID(created["id"]),
                start_time=start,
                end_time=start + timedelta(minutes=45),
                total_price=Decimal("5000"),
            )
        )
        await db.flush()

        response = await client.delete(f"/api/v1/services/{created['id']}", headers=headers)

        assert response.status_code == 409
        assert response.json()["error"]["details"]["upcoming_appointments"] == 1
