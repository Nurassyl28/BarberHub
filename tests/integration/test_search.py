"""Barbershop search: filters, sorting, geo, pagination (docs/SPEC.md §7).

Every test works inside its own city and scopes its queries to it. The database
is shared and long-lived, so an unscoped assertion about "the results" is really
an assertion about every row anyone ever left behind.
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Barbershop, Service, User, UserRole

UserFactory = Callable[..., Awaitable[User]]
ShopFactory = Callable[..., Awaitable[Barbershop]]
BarberFactory = Callable[..., Awaitable[Any]]

BASE = "/api/v1/barbershops"

# Real coordinates, so these distances can be checked by hand.
ALMATY = (Decimal("43.238949"), Decimal("76.889709"))
ALMATY_SUBURB = (Decimal("43.300000"), Decimal("76.889709"))  # ~6.8 km north
ASTANA = (Decimal("51.169392"), Decimal("71.449074"))  # ~970 km away


async def search(client: AsyncClient, city: str, **params: Any) -> list[dict[str, Any]]:
    response = await client.get(BASE, params={"limit": 100, "city": city} | params)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def names(client: AsyncClient, city: str, **params: Any) -> list[str]:
    return [item["name"] for item in await search(client, city, **params)]


async def add_service(db: AsyncSession, shop: Barbershop, name: str, active: bool = True) -> None:
    db.add(
        Service(
            shop_id=shop.id,
            name=name,
            duration_minutes=45,
            price=Decimal("5000"),
            is_active=active,
        )
    )
    await db.flush()


class TestCityFilter:
    async def test_matches_exactly(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        await make_shop(owner, name="In Town", city=city)
        await make_shop(owner, name="Out Of Town")

        assert await names(client, city) == ["In Town"]

    async def test_is_case_insensitive(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        await make_shop(owner, name="In Town", city=city)

        assert await names(client, city.upper()) == ["In Town"]

    async def test_surrounding_whitespace_is_ignored(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        await make_shop(owner, name="In Town", city=city)

        assert await names(client, f"  {city} ") == ["In Town"]

    async def test_a_partial_city_does_not_match(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        """`city` is an exact match, not a prefix search."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        await make_shop(owner, name="In Town", city=city)

        assert await names(client, city[:8]) == []


class TestTextSearch:
    async def test_matches_the_name(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        await make_shop(owner, name="Sharp Fades", city=city)
        await make_shop(owner, name="Classic Cuts", city=city)

        assert await names(client, city, q="fade") == ["Sharp Fades"]

    async def test_matches_the_description(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        await make_shop(
            owner, name="Quiet Place", city=city, description="Specialists in beard sculpting"
        )
        await make_shop(owner, name="Other", city=city, description="Nothing relevant")

        assert await names(client, city, q="beard") == ["Quiet Place"]

    async def test_is_case_insensitive(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        await make_shop(owner, name="Sharp Fades", city=city)

        assert await names(client, city, q="SHARP") == ["Sharp Fades"]

    async def test_a_percent_sign_is_not_a_wildcard(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        """Unescaped, `%` would turn every search into "match everything"."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        await make_shop(owner, name="Cut 100% Sharp", city=city)
        await make_shop(owner, name="Nothing Alike", city=city)

        assert await names(client, city, q="100%") == ["Cut 100% Sharp"]

    async def test_an_underscore_is_not_a_wildcard(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        await make_shop(owner, name="Barber_Hub", city=city)
        await make_shop(owner, name="BarberXHub", city=city)

        assert await names(client, city, q="Barber_Hub") == ["Barber_Hub"]


class TestServiceFilter:
    async def test_finds_shops_offering_the_service(
        self,
        client: AsyncClient,
        city: str,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        await add_service(db, await make_shop(owner, name="Has It", city=city), "Beard Trim")
        await add_service(db, await make_shop(owner, name="Lacks It", city=city), "Haircut")

        assert await names(client, city, service="beard") == ["Has It"]

    async def test_retired_services_do_not_count(
        self,
        client: AsyncClient,
        city: str,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner, name="Used To", city=city)
        await add_service(db, shop, "Beard Trim", active=False)

        assert await names(client, city, service="beard") == []

    async def test_a_shop_appears_once_despite_several_matches(
        self,
        client: AsyncClient,
        city: str,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
    ) -> None:
        """A JOIN would duplicate the row; the filter uses EXISTS instead."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner, name="Many Cuts", city=city)
        for name in ("Beard Trim", "Beard Sculpt", "Beard Colour"):
            await add_service(db, shop, name)

        assert await names(client, city, service="beard") == ["Many Cuts"]


class TestRatingFilter:
    async def _rated(
        self,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
        name: str,
        rating: str,
        city: str,
    ) -> Barbershop:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner, name=name, city=city)
        await make_barber(
            await make_user(role=UserRole.BARBER),
            shop,
            rating=Decimal(rating),
            reviews_count=5,
        )
        return shop

    async def test_filters_below_the_threshold(
        self,
        client: AsyncClient,
        city: str,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
    ) -> None:
        await self._rated(make_user, make_shop, make_barber, "Great", "4.80", city)
        await self._rated(make_user, make_shop, make_barber, "Mediocre", "3.10", city)

        assert await names(client, city, min_rating=4) == ["Great"]

    async def test_the_threshold_is_inclusive(
        self,
        client: AsyncClient,
        city: str,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
    ) -> None:
        await self._rated(make_user, make_shop, make_barber, "Exactly Four", "4.00", city)

        assert await names(client, city, min_rating=4) == ["Exactly Four"]

    async def test_unrated_shops_are_excluded(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        await make_shop(owner, name="Brand New", city=city)

        assert await names(client, city, min_rating=1) == []

    async def test_out_of_range_rating_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get(BASE, params={"min_rating": 9})

        assert response.status_code == 422


class TestGeo:
    async def _placed(
        self,
        make_user: UserFactory,
        make_shop: ShopFactory,
        name: str,
        point: tuple[Decimal, Decimal],
        city: str,
    ) -> Barbershop:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        return await make_shop(owner, name=name, city=city, latitude=point[0], longitude=point[1])

    async def test_distance_is_reported_when_an_origin_is_given(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        await self._placed(make_user, make_shop, "Downtown", ALMATY, city)

        items = await search(client, city, lat=str(ALMATY[0]), lng=str(ALMATY[1]))

        assert items[0]["distance_km"] is not None
        assert items[0]["distance_km"] < 0.1  # the origin itself

    async def test_the_origin_itself_does_not_blow_up_acos(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        """Rounding can push cos() a hair past 1, and PostgreSQL answers that
        with a hard error rather than a NaN — so the expression clamps it."""
        await self._placed(make_user, make_shop, "Exactly Here", ALMATY, city)

        response = await client.get(
            BASE, params={"city": city, "lat": str(ALMATY[0]), "lng": str(ALMATY[1])}
        )

        assert response.status_code == 200

    async def test_distance_is_absent_without_an_origin(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        await self._placed(make_user, make_shop, "Downtown", ALMATY, city)

        items = await search(client, city)

        assert all(item["distance_km"] is None for item in items)

    async def test_radius_excludes_distant_shops(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        await self._placed(make_user, make_shop, "Near", ALMATY, city)
        await self._placed(make_user, make_shop, "Far", ASTANA, city)

        found = await names(client, city, lat=str(ALMATY[0]), lng=str(ALMATY[1]), radius_km=50)

        assert found == ["Near"]

    async def test_radius_includes_a_shop_just_inside_it(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        await self._placed(make_user, make_shop, "Suburb", ALMATY_SUBURB, city)

        found = await names(client, city, lat=str(ALMATY[0]), lng=str(ALMATY[1]), radius_km=10)

        assert found == ["Suburb"]

    async def test_a_tight_radius_excludes_that_same_shop(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        await self._placed(make_user, make_shop, "Suburb", ALMATY_SUBURB, city)

        found = await names(client, city, lat=str(ALMATY[0]), lng=str(ALMATY[1]), radius_km=3)

        assert found == []

    async def test_shops_without_coordinates_are_skipped(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        await make_shop(owner, name="No Coordinates", city=city)

        found = await names(client, city, lat=str(ALMATY[0]), lng=str(ALMATY[1]))

        assert found == []

    async def test_sorting_by_distance(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        await self._placed(make_user, make_shop, "Far", ASTANA, city)
        await self._placed(make_user, make_shop, "Near", ALMATY, city)
        await self._placed(make_user, make_shop, "Middle", ALMATY_SUBURB, city)

        found = await names(client, city, lat=str(ALMATY[0]), lng=str(ALMATY[1]), sort="distance")

        assert found == ["Near", "Middle", "Far"]

    async def test_lat_without_lng_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get(BASE, params={"lat": "43.2"})

        assert response.status_code == 422
        assert "together" in str(response.json()["error"]["details"])

    async def test_radius_without_an_origin_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get(BASE, params={"radius_km": 5})

        assert response.status_code == 422

    async def test_sort_by_distance_without_an_origin_is_rejected(
        self, client: AsyncClient
    ) -> None:
        response = await client.get(BASE, params={"sort": "distance"})

        assert response.status_code == 422


class TestSorting:
    async def test_sorts_by_name(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        for name in ("Zenith", "Apex", "Middle"):
            await make_shop(owner, name=name, city=city)

        assert await names(client, city, sort="name") == ["Apex", "Middle", "Zenith"]

    async def test_sorts_by_name_descending(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        for name in ("Zenith", "Apex", "Middle"):
            await make_shop(owner, name=name, city=city)

        assert await names(client, city, sort="-name") == ["Zenith", "Middle", "Apex"]

    async def test_sorts_by_rating_descending_by_default(
        self,
        client: AsyncClient,
        city: str,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
    ) -> None:
        for name, rating in (("Low", "2.00"), ("High", "5.00"), ("Mid", "3.50")):
            owner = await make_user(role=UserRole.SHOP_OWNER)
            shop = await make_shop(owner, name=name, city=city)
            await make_barber(
                await make_user(role=UserRole.BARBER),
                shop,
                rating=Decimal(rating),
                reviews_count=3,
            )

        assert await names(client, city) == ["High", "Mid", "Low"]

    async def test_an_unknown_sort_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get(BASE, params={"sort": "popularity"})

        assert response.status_code == 422


class TestComposition:
    async def test_filters_narrow_each_other(
        self,
        client: AsyncClient,
        city: str,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
        make_barber: BarberFactory,
    ) -> None:
        """The combination from the spec: city + service + min_rating + sort."""
        for name, shop_city, rating, with_service in (
            ("Target", city, "4.90", True),
            ("Wrong City", None, "4.90", True),
            ("Low Rated", city, "2.00", True),
            ("No Haircut", city, "5.00", False),
        ):
            owner = await make_user(role=UserRole.SHOP_OWNER)
            shop = (
                await make_shop(owner, name=name, city=shop_city)
                if shop_city
                else await make_shop(owner, name=name)
            )
            await make_barber(
                await make_user(role=UserRole.BARBER),
                shop,
                rating=Decimal(rating),
                reviews_count=4,
            )
            if with_service:
                await add_service(db, shop, "Haircut")

        found = await names(client, city, service="Haircut", min_rating=4, sort="-rating")

        assert found == ["Target"]

    async def test_pagination_is_stable_across_ties(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        """Every shop here ties on rating; without a tiebreak, paging would
        repeat and skip rows."""
        owner = await make_user(role=UserRole.SHOP_OWNER)
        for i in range(6):
            await make_shop(owner, name=f"Tied {i}", city=city)

        first = await names(client, city, page=1, limit=3)
        second = await names(client, city, page=2, limit=3)

        assert len(first) == len(second) == 3
        assert set(first).isdisjoint(second)
        assert len(set(first) | set(second)) == 6

    async def test_the_total_reflects_the_filters(
        self, client: AsyncClient, city: str, make_user: UserFactory, make_shop: ShopFactory
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        for i in range(3):
            await make_shop(owner, name=f"Counted {i}", city=city)
        await make_shop(owner, name="Elsewhere")

        body = (await client.get(BASE, params={"city": city, "limit": 2})).json()

        assert body["total"] == 3
        assert body["pages"] == 2
        assert len(body["items"]) == 2

    async def test_soft_deleted_shops_never_appear(
        self,
        client: AsyncClient,
        city: str,
        db: AsyncSession,
        make_user: UserFactory,
        make_shop: ShopFactory,
    ) -> None:
        owner = await make_user(role=UserRole.SHOP_OWNER)
        shop = await make_shop(owner, name="Gone", city=city)
        shop.is_active = False
        await db.flush()

        assert await names(client, city) == []
