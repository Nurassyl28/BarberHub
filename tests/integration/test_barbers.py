"""Barbers: joining a shop, profile edits, service assignment (docs/SPEC.md §6)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, Barber, Barbershop, Service, User, UserRole
from app.utils.time import utcnow

UserFactory = Callable[..., Awaitable[User]]
ShopFactory = Callable[..., Awaitable[Barbershop]]
BarberFactory = Callable[..., Awaitable[Barber]]
HeaderFactory = Callable[[User], dict[str, str]]


def shop_barbers(shop_id: uuid.UUID) -> str:
    return f"/api/v1/barbershops/{shop_id}/barbers"


async def _make_service(
    db: AsyncSession, shop: Barbershop, name: str = "Haircut", **kw: object
) -> Service:
    service = Service(
        shop_id=shop.id,
        name=name,
        duration_minutes=45,
        price=Decimal("5000"),
        **kw,  # type: ignore[arg-type]
    )
    db.add(service)
    await db.flush()
    return service


class TestAddBarber:
    async def test_owner_adds_an_existing_account(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        hire = await make_user(role=UserRole.CUSTOMER)

        response = await client.post(
            shop_barbers(shop.id),
            json={"email": hire.email, "experience_years": 3},
            headers=auth_headers(owner),
        )

        assert response.status_code == 201
        body = response.json()
        assert body["user_id"] == str(hire.id)
        assert body["experience_years"] == 3
        assert body["first_name"] == hire.first_name

    async def test_customer_account_is_promoted_to_barber(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        hire = await make_user(role=UserRole.CUSTOMER)

        await client.post(
            shop_barbers(shop.id), json={"email": hire.email}, headers=auth_headers(owner)
        )
        await db.refresh(hire)

        assert hire.role is UserRole.BARBER

    async def test_a_shop_owner_who_also_cuts_keeps_their_role(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """Promotion must never be a demotion."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)

        response = await client.post(
            shop_barbers(shop.id), json={"email": owner.email}, headers=auth_headers(owner)
        )
        await db.refresh(owner)

        assert response.status_code == 201
        assert owner.role is UserRole.SHOP_OWNER

    async def test_admin_account_is_not_demoted(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        admin = await make_user(role=UserRole.ADMIN)

        await client.post(
            shop_barbers(shop.id), json={"email": admin.email}, headers=auth_headers(owner)
        )
        await db.refresh(admin)

        assert admin.role is UserRole.ADMIN

    async def test_unknown_email_is_404(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """This endpoint never creates accounts, so it cannot be used to farm them."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)

        response = await client.post(
            shop_barbers(shop.id),
            json={"email": "nobody@example.com"},
            headers=auth_headers(owner),
        )

        assert response.status_code == 404

    async def test_adding_the_same_person_twice_conflicts(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        hire = await make_user()
        headers = auth_headers(owner)
        await client.post(shop_barbers(shop.id), json={"email": hire.email}, headers=headers)

        response = await client.post(
            shop_barbers(shop.id), json={"email": hire.email}, headers=headers
        )

        assert response.status_code == 409

    async def test_the_same_person_may_work_at_two_shops(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        first = await make_shop(owner, name="One")
        second = await make_shop(owner, name="Two")
        hire = await make_user()
        headers = auth_headers(owner)
        await client.post(shop_barbers(first.id), json={"email": hire.email}, headers=headers)

        response = await client.post(
            shop_barbers(second.id), json={"email": hire.email}, headers=headers
        )

        assert response.status_code == 201

    async def test_another_owner_cannot_add(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        intruder = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        hire = await make_user()

        response = await client.post(
            shop_barbers(shop.id), json={"email": hire.email}, headers=auth_headers(intruder)
        )

        assert response.status_code == 403


class TestReadBarbers:
    async def test_list_is_public(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)

        response = await client.get(shop_barbers(shop.id))

        assert response.status_code == 200
        assert [b["id"] for b in response.json()] == [str(barber.id)]

    async def test_public_list_hides_removed_barbers(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        await client.delete(f"/api/v1/barbers/{barber.id}", headers=auth_headers(owner))

        assert (await client.get(shop_barbers(shop.id))).json() == []

    async def test_owner_still_sees_removed_barbers(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        headers = auth_headers(owner)
        await client.delete(f"/api/v1/barbers/{barber.id}", headers=headers)

        listed = (await client.get(shop_barbers(shop.id), headers=headers)).json()

        assert [b["id"] for b in listed] == [str(barber.id)]
        assert listed[0]["is_active"] is False

    async def test_detail_is_public(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop, bio="10 years")

        response = await client.get(f"/api/v1/barbers/{barber.id}")

        assert response.status_code == 200
        assert response.json()["bio"] == "10 years"

    async def test_unknown_barber_is_404(self, client: AsyncClient) -> None:
        assert (await client.get(f"/api/v1/barbers/{uuid.uuid4()}")).status_code == 404


class TestUpdateBarber:
    async def test_barber_can_edit_their_own_bio(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber_user = await make_user(role=UserRole.BARBER)
        barber = await make_barber(barber_user, shop)

        response = await client.patch(
            f"/api/v1/barbers/{barber.id}",
            json={"bio": "Fades and beards"},
            headers=auth_headers(barber_user),
        )

        assert response.status_code == 200
        assert response.json()["bio"] == "Fades and beards"

    async def test_owner_can_edit_their_barber(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)

        response = await client.patch(
            f"/api/v1/barbers/{barber.id}",
            json={"experience_years": 7},
            headers=auth_headers(owner),
        )

        assert response.status_code == 200
        assert response.json()["experience_years"] == 7

    async def test_a_different_barber_cannot_edit(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        colleague = await make_user(role=UserRole.BARBER)
        await make_barber(colleague, shop)

        response = await client.patch(
            f"/api/v1/barbers/{barber.id}",
            json={"bio": "hijacked"},
            headers=auth_headers(colleague),
        )

        assert response.status_code == 403

    async def test_barber_cannot_remove_themselves(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """Deletion is stricter than editing: leaving strands booked customers."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber_user = await make_user(role=UserRole.BARBER)
        barber = await make_barber(barber_user, shop)

        response = await client.delete(
            f"/api/v1/barbers/{barber.id}", headers=auth_headers(barber_user)
        )

        assert response.status_code == 403


class TestDeleteBarber:
    async def test_owner_can_remove(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)

        response = await client.delete(f"/api/v1/barbers/{barber.id}", headers=auth_headers(owner))

        assert response.status_code == 204

    async def test_refused_while_upcoming_appointments_exist(
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
        service = await _make_service(db, shop)
        start = utcnow() + timedelta(days=1)
        db.add(
            Appointment(
                customer_id=(await make_user()).id,
                barber_id=barber.id,
                service_id=service.id,
                start_time=start,
                end_time=start + timedelta(minutes=45),
                total_price=Decimal("5000"),
            )
        )
        await db.flush()

        response = await client.delete(f"/api/v1/barbers/{barber.id}", headers=auth_headers(owner))

        assert response.status_code == 409
        assert response.json()["error"]["details"]["upcoming_appointments"] == 1


class TestBarberServices:
    async def test_owner_assigns_services(
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
        haircut = await _make_service(db, shop, "Haircut")
        beard = await _make_service(db, shop, "Beard Trim")

        response = await client.put(
            f"/api/v1/barbers/{barber.id}/services",
            json={"service_ids": [str(haircut.id), str(beard.id)]},
            headers=auth_headers(owner),
        )

        assert response.status_code == 200
        assert {s["id"] for s in response.json()["services"]} == {
            str(haircut.id),
            str(beard.id),
        }

    async def test_assignment_replaces_rather_than_appends(
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
        haircut = await _make_service(db, shop, "Haircut")
        beard = await _make_service(db, shop, "Beard Trim")
        headers = auth_headers(owner)
        url = f"/api/v1/barbers/{barber.id}/services"
        await client.put(url, json={"service_ids": [str(haircut.id)]}, headers=headers)

        body = (
            await client.put(url, json={"service_ids": [str(beard.id)]}, headers=headers)
        ).json()

        assert [s["id"] for s in body["services"]] == [str(beard.id)]

    async def test_empty_list_clears_assignments(
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
        haircut = await _make_service(db, shop)
        headers = auth_headers(owner)
        url = f"/api/v1/barbers/{barber.id}/services"
        await client.put(url, json={"service_ids": [str(haircut.id)]}, headers=headers)

        body = (await client.put(url, json={"service_ids": []}, headers=headers)).json()

        assert body["services"] == []

    async def test_service_from_another_shop_is_rejected(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """No CHECK can express this rule — it spans two tables (§3)."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner, name="Mine")
        other_shop = await make_shop(owner, name="Other")
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        foreign = await _make_service(db, other_shop)

        response = await client.put(
            f"/api/v1/barbers/{barber.id}/services",
            json={"service_ids": [str(foreign.id)]},
            headers=auth_headers(owner),
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["belong_to_another_shop"] == [str(foreign.id)]

    async def test_unknown_service_is_rejected(
        self,
        client: AsyncClient,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        ghost = uuid.uuid4()

        response = await client.put(
            f"/api/v1/barbers/{barber.id}/services",
            json={"service_ids": [str(ghost)]},
            headers=auth_headers(owner),
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["unknown"] == [str(ghost)]

    async def test_retired_service_is_rejected(
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
        retired = await _make_service(db, shop, is_active=False)

        response = await client.put(
            f"/api/v1/barbers/{barber.id}/services",
            json={"service_ids": [str(retired.id)]},
            headers=auth_headers(owner),
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["inactive"] == [str(retired.id)]

    async def test_barber_cannot_assign_their_own_services(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """What a barber is allowed to sell is the owner's call, not theirs."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber_user = await make_user(role=UserRole.BARBER)
        barber = await make_barber(barber_user, shop)
        haircut = await _make_service(db, shop)

        response = await client.put(
            f"/api/v1/barbers/{barber.id}/services",
            json={"service_ids": [str(haircut.id)]},
            headers=auth_headers(barber_user),
        )

        assert response.status_code == 403

    async def test_duplicate_ids_are_collapsed(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """Without de-duping this hits the composite primary key and 500s."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner)
        barber = await make_barber(await make_user(role=UserRole.BARBER), shop)
        haircut = await _make_service(db, shop)

        response = await client.put(
            f"/api/v1/barbers/{barber.id}/services",
            json={"service_ids": [str(haircut.id), str(haircut.id)]},
            headers=auth_headers(owner),
        )

        assert response.status_code == 200
        assert len(response.json()["services"]) == 1
