"""Barbershop CRUD, ownership enforcement, and soft delete (docs/SPEC.md §6)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, AppointmentStatus, Barber, Barbershop, Service, User, UserRole
from app.utils.time import utcnow
from tests.conftest import shop_payload

UserFactory = Callable[..., Awaitable[User]]
ShopFactory = Callable[..., Awaitable[Barbershop]]
BarberFactory = Callable[..., Awaitable[Barber]]
HeaderFactory = Callable[[User], dict[str, str]]

BASE = "/api/v1/barbershops"


class TestCreate:
    async def test_owner_can_create(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)

        response = await client.post(BASE, json=shop_payload(), headers=auth_headers(owner))

        assert response.status_code == 201
        body = response.json()
        assert body["owner_id"] == str(owner.id)
        assert body["is_active"] is True
        assert body["timezone"] == "Asia/Almaty"

    async def test_owner_id_cannot_be_spoofed_in_the_body(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        """`owner_id` comes from the token, never from the request body."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        victim = await make_user(role=UserRole.SHOP_OWNER)

        response = await client.post(
            BASE,
            json=shop_payload(owner_id=str(victim.id)),
            headers=auth_headers(owner),
        )

        assert response.status_code == 201
        assert response.json()["owner_id"] == str(owner.id)

    async def test_customer_cannot_create(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        customer = await make_user(role=UserRole.CUSTOMER)

        response = await client.post(BASE, json=shop_payload(), headers=auth_headers(customer))

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_admin_can_create(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        admin = await make_user(role=UserRole.ADMIN)

        response = await client.post(BASE, json=shop_payload(), headers=auth_headers(admin))

        assert response.status_code == 201

    async def test_anonymous_cannot_create(self, client: AsyncClient) -> None:
        response = await client.post(BASE, json=shop_payload())

        assert response.status_code == 401

    async def test_unknown_timezone_is_rejected(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        """Caught at the edge, not later inside the availability engine."""
        owner = await make_user(role=UserRole.SHOP_OWNER)

        response = await client.post(
            BASE,
            json=shop_payload(timezone="Mars/Olympus_Mons"),
            headers=auth_headers(owner),
        )

        assert response.status_code == 422

    async def test_out_of_range_coordinates_are_rejected(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)

        response = await client.post(
            BASE,
            json=shop_payload(latitude="91.0", longitude="10.0"),
            headers=auth_headers(owner),
        )

        assert response.status_code == 422


class TestRead:
    async def test_list_is_public(
        self, client: AsyncClient, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner, name="Public Shop")

        response = await client.get(BASE, params={"limit": 100})

        assert response.status_code == 200
        body = response.json()
        assert {"items", "total", "page", "limit", "pages"} <= body.keys()
        assert str(shop.id) in [item["id"] for item in body["items"]]

    async def test_detail_is_public(
        self, client: AsyncClient, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)

        response = await client.get(f"{BASE}/{shop.id}")

        assert response.status_code == 200
        assert response.json()["id"] == str(shop.id)

    async def test_unknown_id_is_404(self, client: AsyncClient) -> None:
        response = await client.get(f"{BASE}/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_malformed_id_is_422(self, client: AsyncClient) -> None:
        response = await client.get(f"{BASE}/not-a-uuid")

        assert response.status_code == 422

    async def test_pagination_splits_results(
        self, client: AsyncClient, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        for i in range(3):
            await make_shop(owner, name=f"Shop {i}")

        first = (await client.get(BASE, params={"page": 1, "limit": 2})).json()

        assert len(first["items"]) == 2
        assert first["pages"] == -(-first["total"] // 2)

    async def test_limit_is_capped(self, client: AsyncClient) -> None:
        response = await client.get(BASE, params={"limit": 1000})

        assert response.status_code == 422

    async def test_rating_ignores_barbers_without_reviews(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
    ) -> None:
        """A new barber sitting at 0.00 must not drag the shop's average down."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        rated_user = await make_user(role=UserRole.BARBER)
        fresh_user = await make_user(role=UserRole.BARBER)
        await make_barber(rated_user, shop, rating=Decimal("4.50"), reviews_count=10)
        await make_barber(fresh_user, shop)
        await db.flush()

        body = (await client.get(f"{BASE}/{shop.id}")).json()

        assert Decimal(body["rating"]) == Decimal("4.50")
        assert body["reviews_count"] == 10
        assert body["barbers_count"] == 2

    async def test_shop_without_barbers_rates_zero(
        self, client: AsyncClient, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)

        body = (await client.get(f"{BASE}/{shop.id}")).json()

        assert Decimal(body["rating"]) == Decimal("0")
        assert body["barbers_count"] == 0


class TestUpdate:
    async def test_owner_can_update(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)

        response = await client.patch(
            f"{BASE}/{shop.id}", json={"name": "Renamed"}, headers=auth_headers(owner)
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"

    async def test_partial_update_leaves_other_fields_alone(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner, city="Astana", phone="+7700")

        body = (
            await client.patch(
                f"{BASE}/{shop.id}", json={"name": "Renamed"}, headers=auth_headers(owner)
            )
        ).json()

        assert body["city"] == "Astana"
        assert body["phone"] == "+7700"

    async def test_explicit_null_clears_a_field(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """`exclude_unset` must distinguish "absent" from "explicitly null"."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner, phone="+7700")

        body = (
            await client.patch(
                f"{BASE}/{shop.id}", json={"phone": None}, headers=auth_headers(owner)
            )
        ).json()

        assert body["phone"] is None

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

        response = await client.patch(
            f"{BASE}/{shop.id}", json={"name": "Hijacked"}, headers=auth_headers(intruder)
        )

        assert response.status_code == 403

    async def test_customer_cannot_update(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        customer = await make_user(role=UserRole.CUSTOMER)

        response = await client.patch(
            f"{BASE}/{shop.id}", json={"name": "Hijacked"}, headers=auth_headers(customer)
        )

        assert response.status_code == 403

    async def test_admin_can_update_any_shop(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        admin = await make_user(role=UserRole.ADMIN)

        response = await client.patch(
            f"{BASE}/{shop.id}", json={"name": "By Admin"}, headers=auth_headers(admin)
        )

        assert response.status_code == 200


class TestSoftDelete:
    async def test_owner_can_deactivate(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)

        response = await client.delete(f"{BASE}/{shop.id}", headers=auth_headers(owner))

        assert response.status_code == 204

    async def test_deactivated_shop_disappears_from_the_public_list(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        await client.delete(f"{BASE}/{shop.id}", headers=auth_headers(owner))

        listed = (await client.get(BASE, params={"limit": 100})).json()["items"]

        assert str(shop.id) not in [item["id"] for item in listed]

    async def test_deactivated_shop_is_404_for_strangers(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        await client.delete(f"{BASE}/{shop.id}", headers=auth_headers(owner))

        assert (await client.get(f"{BASE}/{shop.id}")).status_code == 404

    async def test_owner_still_sees_their_deactivated_shop(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        await client.delete(f"{BASE}/{shop.id}", headers=auth_headers(owner))

        response = await client.get(f"{BASE}/{shop.id}", headers=auth_headers(owner))

        assert response.status_code == 200
        assert response.json()["is_active"] is False

    async def test_owner_can_reactivate(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        await client.delete(f"{BASE}/{shop.id}", headers=auth_headers(owner))

        response = await client.patch(
            f"{BASE}/{shop.id}", json={"is_active": True}, headers=auth_headers(owner)
        )

        assert response.status_code == 200
        assert response.json()["is_active"] is True

    async def test_another_owner_cannot_deactivate(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        intruder = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)

        response = await client.delete(f"{BASE}/{shop.id}", headers=auth_headers(intruder))

        assert response.status_code == 403

    async def test_refused_while_upcoming_appointments_exist(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """A shop cannot vanish out from under customers who hold bookings (§11.9)."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        customer = await make_user(role=UserRole.CUSTOMER)
        service = Service(
            shop_id=shop.id, name="Haircut", duration_minutes=45, price=Decimal("5000")
        )
        db.add(service)
        await db.flush()
        start = utcnow() + timedelta(days=2)
        db.add(
            Appointment(
                customer_id=customer.id,
                barber_id=barber.id,
                service_id=service.id,
                start_time=start,
                end_time=start + timedelta(minutes=45),
                total_price=Decimal("5000"),
            )
        )
        await db.flush()

        response = await client.delete(f"{BASE}/{shop.id}", headers=auth_headers(owner))

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"
        assert response.json()["error"]["details"]["upcoming_appointments"] == 1

    async def test_allowed_when_only_past_or_cancelled_appointments_exist(
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
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        customer = await make_user(role=UserRole.CUSTOMER)
        service = Service(
            shop_id=shop.id, name="Haircut", duration_minutes=45, price=Decimal("5000")
        )
        db.add(service)
        await db.flush()

        past = utcnow() - timedelta(days=3)
        future = utcnow() + timedelta(days=3)
        common: dict[str, Any] = {
            "customer_id": customer.id,
            "barber_id": barber.id,
            "service_id": service.id,
            "total_price": Decimal("5000"),
        }
        db.add_all(
            [
                Appointment(
                    start_time=past,
                    end_time=past + timedelta(minutes=45),
                    status=AppointmentStatus.COMPLETED,
                    **common,
                ),
                Appointment(
                    start_time=future,
                    end_time=future + timedelta(minutes=45),
                    status=AppointmentStatus.CANCELLED,
                    **common,
                ),
            ]
        )
        await db.flush()

        response = await client.delete(f"{BASE}/{shop.id}", headers=auth_headers(owner))

        assert response.status_code == 204
