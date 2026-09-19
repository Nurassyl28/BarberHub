"""Reviews and the denormalized barber rating (docs/SPEC.md §6, §11.3)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, AppointmentStatus, Review, User

Bookable = Callable[..., Awaitable[dict[str, Any]]]
UserFactory = Callable[..., Awaitable[User]]
HeaderFactory = Callable[[User], dict[str, str]]


async def completed_appointment(
    db: AsyncSession, fx: dict[str, Any], customer: User | None = None, hours_ago: int = 2
) -> Appointment:
    """A finished haircut, inserted directly — the booking path is tested elsewhere."""
    start = datetime.now(UTC) - timedelta(hours=hours_ago)
    appointment = Appointment(
        customer_id=(customer or fx["customer"]).id,
        barber_id=fx["barber"].id,
        service_id=fx["service"].id,
        start_time=start,
        end_time=start + timedelta(minutes=45),
        total_price=Decimal("5000"),
        status=AppointmentStatus.COMPLETED,
    )
    db.add(appointment)
    await db.flush()
    return appointment


def review_url(appointment_id: uuid.UUID) -> str:
    return f"/api/v1/appointments/{appointment_id}/review"


class TestCreate:
    async def test_customer_reviews_a_completed_appointment(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)

        response = await client.post(
            review_url(appointment.id),
            json={"rating": 5, "comment": "Best fade in Almaty"},
            headers=auth_headers(fx["customer"]),
        )

        assert response.status_code == 201
        body = response.json()
        assert body["rating"] == 5
        assert body["comment"] == "Best fade in Almaty"

    async def test_the_author_is_shown_as_first_name_and_initial(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """Reviews are public; a full name plus a dated haircut is more than
        the customer signed up to publish."""
        fx = await bookable()
        customer = await make_user(first_name="Dana", last_name="Tulegenova")
        appointment = await completed_appointment(db, fx, customer)

        body = (
            await client.post(
                review_url(appointment.id),
                json={"rating": 5},
                headers=auth_headers(customer),
            )
        ).json()

        assert body["author"] == "Dana T."
        assert "Tulegenova" not in str(body)

    async def test_a_comment_is_optional(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)

        response = await client.post(
            review_url(appointment.id),
            json={"rating": 4},
            headers=auth_headers(fx["customer"]),
        )

        assert response.status_code == 201
        assert response.json()["comment"] is None

    async def test_a_confirmed_appointment_cannot_be_reviewed(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)
        appointment.status = AppointmentStatus.CONFIRMED
        await db.flush()

        response = await client.post(
            review_url(appointment.id),
            json={"rating": 5},
            headers=auth_headers(fx["customer"]),
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_TRANSITION"

    async def test_a_no_show_cannot_be_reviewed(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)
        appointment.status = AppointmentStatus.NO_SHOW
        await db.flush()

        response = await client.post(
            review_url(appointment.id),
            json={"rating": 1},
            headers=auth_headers(fx["customer"]),
        )

        assert response.status_code == 409

    async def test_only_the_appointments_customer_may_review(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)

        response = await client.post(
            review_url(appointment.id),
            json={"rating": 1},
            headers=auth_headers(await make_user()),
        )

        assert response.status_code == 403

    async def test_the_barber_cannot_review_themselves(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)

        response = await client.post(
            review_url(appointment.id),
            json={"rating": 5},
            headers=auth_headers(fx["barber_user"]),
        )

        assert response.status_code == 403

    async def test_reviewing_twice_is_refused(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)
        headers = auth_headers(fx["customer"])
        await client.post(review_url(appointment.id), json={"rating": 5}, headers=headers)

        response = await client.post(
            review_url(appointment.id), json={"rating": 1}, headers=headers
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ALREADY_REVIEWED"

    async def test_a_failed_second_review_does_not_corrupt_the_rating(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        """The rollback must not leave a count that disagrees with the rows."""
        fx = await bookable()
        appointment = await completed_appointment(db, fx)
        headers = auth_headers(fx["customer"])
        await client.post(review_url(appointment.id), json={"rating": 5}, headers=headers)
        await client.post(review_url(appointment.id), json={"rating": 1}, headers=headers)

        await db.refresh(fx["barber"])
        stored = (
            await db.execute(
                select(func.count()).select_from(Review).where(Review.barber_id == fx["barber"].id)
            )
        ).scalar_one()

        assert fx["barber"].reviews_count == stored == 1
        assert fx["barber"].rating == Decimal("5.00")

    async def test_unknown_appointment_is_404(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        response = await client.post(
            review_url(uuid.uuid4()),
            json={"rating": 5},
            headers=auth_headers(await make_user()),
        )

        assert response.status_code == 404

    async def test_anonymous_cannot_review(
        self, client: AsyncClient, db: AsyncSession, bookable: Bookable
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)

        response = await client.post(review_url(appointment.id), json={"rating": 5})

        assert response.status_code == 401

    async def test_rating_bounds_are_enforced(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)
        headers = auth_headers(fx["customer"])

        for bad in (0, 6, -1):
            response = await client.post(
                review_url(appointment.id), json={"rating": bad}, headers=headers
            )
            assert response.status_code == 422, bad


class TestRatingAggregate:
    async def test_the_first_review_sets_the_rating(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)

        await client.post(
            review_url(appointment.id),
            json={"rating": 4},
            headers=auth_headers(fx["customer"]),
        )
        await db.refresh(fx["barber"])

        assert fx["barber"].rating == Decimal("4.00")
        assert fx["barber"].reviews_count == 1

    async def test_the_rating_is_the_mean_of_every_review(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        for score in (5, 4, 3):
            customer = await make_user()
            appointment = await completed_appointment(db, fx, customer)
            await client.post(
                review_url(appointment.id),
                json={"rating": score},
                headers=auth_headers(customer),
            )
        await db.refresh(fx["barber"])

        assert fx["barber"].rating == Decimal("4.00")
        assert fx["barber"].reviews_count == 3

    async def test_the_rating_is_rounded_to_two_places(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """5, 4, 4 averages to 4.333... which must fit NUMERIC(3,2)."""
        fx = await bookable()
        for score in (5, 4, 4):
            customer = await make_user()
            appointment = await completed_appointment(db, fx, customer)
            await client.post(
                review_url(appointment.id),
                json={"rating": score},
                headers=auth_headers(customer),
            )
        await db.refresh(fx["barber"])

        assert fx["barber"].rating == Decimal("4.33")

    async def test_the_stored_rating_matches_a_fresh_recomputation(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """The denormalized column must equal what the reviews actually say."""
        fx = await bookable()
        for score in (5, 3, 4, 2, 5):
            customer = await make_user()
            appointment = await completed_appointment(db, fx, customer)
            await client.post(
                review_url(appointment.id),
                json={"rating": score},
                headers=auth_headers(customer),
            )
        await db.refresh(fx["barber"])

        average, count = (
            await db.execute(
                select(func.avg(Review.rating), func.count()).where(
                    Review.barber_id == fx["barber"].id
                )
            )
        ).one()

        assert fx["barber"].reviews_count == count
        assert fx["barber"].rating == Decimal(average).quantize(Decimal("0.01"))

    async def test_another_barbers_reviews_do_not_leak_in(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        mine = await bookable()
        other = await bookable()
        appointment = await completed_appointment(db, other)
        await client.post(
            review_url(appointment.id),
            json={"rating": 1},
            headers=auth_headers(other["customer"]),
        )
        await db.refresh(mine["barber"])

        assert mine["barber"].reviews_count == 0
        assert mine["barber"].rating == Decimal("0.00")

    async def test_the_shop_rating_follows_its_barbers(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)
        await client.post(
            review_url(appointment.id),
            json={"rating": 5},
            headers=auth_headers(fx["customer"]),
        )

        shop = (await client.get(f"/api/v1/barbershops/{fx['shop'].id}")).json()

        assert Decimal(shop["rating"]) == Decimal("5.00")
        assert shop["reviews_count"] == 1

    async def test_the_rating_endpoint_reports_the_summary(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)
        await client.post(
            review_url(appointment.id),
            json={"rating": 3},
            headers=auth_headers(fx["customer"]),
        )

        body = (await client.get(f"/api/v1/barbers/{fx['barber'].id}/rating")).json()

        assert Decimal(body["rating"]) == Decimal("3.00")
        assert body["reviews_count"] == 1


class TestListing:
    async def test_reviews_are_public(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        appointment = await completed_appointment(db, fx)
        await client.post(
            review_url(appointment.id),
            json={"rating": 5, "comment": "Great"},
            headers=auth_headers(fx["customer"]),
        )

        response = await client.get(f"/api/v1/barbers/{fx['barber'].id}/reviews")

        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["comment"] == "Great"

    async def test_newest_reviews_come_first(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        """`created_at` defaults to the *transaction* timestamp, so reviews
        written inside one test transaction all share it. Production gives each
        review its own transaction; here the timestamps are set explicitly so
        the ordering being asserted is real."""
        fx = await bookable()
        created: list[tuple[int, uuid.UUID]] = []
        for score in (2, 3, 4):
            customer = await make_user()
            appointment = await completed_appointment(db, fx, customer)
            body = (
                await client.post(
                    review_url(appointment.id),
                    json={"rating": score},
                    headers=auth_headers(customer),
                )
            ).json()
            created.append((score, uuid.UUID(body["id"])))

        base = datetime(2027, 1, 1, tzinfo=UTC)
        for offset, (_, review_id) in enumerate(created):
            review = await db.get(Review, review_id)
            assert review is not None
            review.created_at = base + timedelta(days=offset)
        await db.flush()

        items = (await client.get(f"/api/v1/barbers/{fx['barber'].id}/reviews")).json()["items"]

        assert [i["rating"] for i in items] == [4, 3, 2]

    async def test_listing_is_paginated(
        self,
        client: AsyncClient,
        db: AsyncSession,
        bookable: Bookable,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        fx = await bookable()
        for _ in range(3):
            customer = await make_user()
            appointment = await completed_appointment(db, fx, customer)
            await client.post(
                review_url(appointment.id),
                json={"rating": 5},
                headers=auth_headers(customer),
            )

        page = (
            await client.get(f"/api/v1/barbers/{fx['barber'].id}/reviews", params={"limit": 2})
        ).json()

        assert len(page["items"]) == 2
        assert page["total"] == 3
        assert page["pages"] == 2

    async def test_a_barber_without_reviews_returns_an_empty_page(
        self, client: AsyncClient, bookable: Bookable
    ) -> None:
        fx = await bookable()

        body = (await client.get(f"/api/v1/barbers/{fx['barber'].id}/reviews")).json()

        assert body == {"items": [], "total": 0, "page": 1, "limit": 20, "pages": 0}

    async def test_unknown_barber_is_404(self, client: AsyncClient) -> None:
        response = await client.get(f"/api/v1/barbers/{uuid.uuid4()}/reviews")

        assert response.status_code == 404
