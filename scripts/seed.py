"""Populate a database with a realistic shop, staff, and a month of history.

Idempotent: run it twice and it re-creates the same demo world rather than
piling up duplicates, so it is safe to re-run while developing.

    uv run python -m scripts.seed
"""

import asyncio
import random
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.session import SessionFactory, engine
from app.models import (
    Appointment,
    AppointmentReminder,
    AppointmentStatus,
    Barber,
    Barbershop,
    Review,
    Service,
    ShopClosure,
    User,
    UserRole,
    WorkingHours,
    barber_services,
)

# `.local` is a reserved TLD that `EmailStr` refuses, so seeded accounts on it
# can be stored but never logged into. `example.com` is the documentation
# domain and validates cleanly.
DEMO_PASSWORD = "Sup3rSecret!"
SHOP_NAME = "Sharp Cuts Almaty"

OWNER = ("Olzhas", "Amanov", "owner@barberhub.example.com")
BARBERS = [
    ("Aidar", "Seitov", "aidar@barberhub.example.com", 8, "Fades and classic scissor work."),
    ("Nurlan", "Bekov", "nurlan@barberhub.example.com", 4, "Beard sculpting specialist."),
    ("Timur", "Zhanov", "timur@barberhub.example.com", 12, "Twelve years behind the chair."),
]
CUSTOMERS = [
    ("Dana", "Tulegenova", "dana@example.com"),
    ("Aruzhan", "Serik", "aruzhan@example.com"),
    ("Yerlan", "Kasymov", "yerlan@example.com"),
    ("Madina", "Ospanova", "madina@example.com"),
    ("Askar", "Nurpeisov", "askar@example.com"),
]
SERVICES = [
    ("Haircut", "Wash, cut and style.", 45, "5000.00"),
    ("Beard Trim", "Shape, line up and hot towel.", 30, "3000.00"),
    ("Haircut + Beard", "The full sit-down.", 60, "7000.00"),
    ("Kids Cut", "Under twelves.", 30, "3500.00"),
]
COMMENTS = [
    "Best fade I have had in Almaty.",
    "Quick, friendly, exactly what I asked for.",
    "Good cut, but I waited ten minutes past my slot.",
    "Been coming here for a year. Never disappointed.",
    "Decent, though a bit pricey.",
    None,
]


async def seed() -> None:
    random.seed(20260918)  # same demo world every run

    async with SessionFactory() as db:
        await _clear_previous(db)

        owner = _user(*OWNER, role=UserRole.SHOP_OWNER)
        barber_users = [
            _user(first, last, email, role=UserRole.BARBER) for first, last, email, _, _ in BARBERS
        ]
        customers = [_user(*c) for c in CUSTOMERS]
        db.add_all([owner, *barber_users, *customers])
        await db.flush()

        shop = Barbershop(
            owner_id=owner.id,
            name=SHOP_NAME,
            description="Traditional barbering on Abay. Walk-ins welcome, booking preferred.",
            address="Abay Avenue 52",
            city="Almaty",
            latitude=Decimal("43.238949"),
            longitude=Decimal("76.889709"),
            phone="+7 727 000 0000",
            timezone="Asia/Almaty",
        )
        db.add(shop)
        await db.flush()

        services = [
            Service(
                shop_id=shop.id,
                name=name,
                description=description,
                duration_minutes=duration,
                price=Decimal(price),
            )
            for name, description, duration, price in SERVICES
        ]
        db.add_all(services)
        await db.flush()

        barbers = []
        for user, (_, _, _, years, bio) in zip(barber_users, BARBERS, strict=True):
            barber = Barber(user_id=user.id, shop_id=shop.id, experience_years=years, bio=bio)
            db.add(barber)
            barbers.append(barber)
        await db.flush()

        for index, barber in enumerate(barbers):
            # The junior barber does not do the long combined service.
            offered = services if index != 1 else [services[1], services[3]]
            for service in offered:
                await db.execute(
                    barber_services.insert().values(barber_id=barber.id, service_id=service.id)
                )
            # Monday-Saturday, with Timur on a split shift.
            for weekday in range(6):
                if index == 2:
                    shifts = [(time(9), time(13)), (time(14), time(19))]
                else:
                    shifts = [(time(10), time(19))]
                for start, end in shifts:
                    db.add(
                        WorkingHours(
                            barber_id=barber.id,
                            weekday=weekday,
                            start_time=start,
                            end_time=end,
                        )
                    )
        await db.flush()

        # A two-day new-year closure, far enough out that it never collides
        # with the generated history.
        closure_start = (datetime.now(UTC) + timedelta(days=40)).date()
        db.add(
            ShopClosure(
                shop_id=shop.id,
                start_date=closure_start,
                end_date=closure_start + timedelta(days=1),
                reason="Annual maintenance",
            )
        )
        await db.flush()

        appointments = await _history(db, barbers, services, customers)
        await _reviews(db, appointments, barbers)
        await db.commit()

    await engine.dispose()
    _report(len(barbers), len(services), len(appointments))


async def _clear_previous(db: AsyncSession) -> None:
    """Remove the previous demo world, leaving anything else untouched."""
    emails = [OWNER[2], *[b[2] for b in BARBERS], *[c[2] for c in CUSTOMERS]]
    shop = (
        await db.execute(select(Barbershop).where(Barbershop.name == SHOP_NAME))
    ).scalar_one_or_none()
    if shop is not None:
        barber_ids = (
            (await db.execute(select(Barber.id).where(Barber.shop_id == shop.id))).scalars().all()
        )
        if barber_ids:
            appointment_ids = (
                (
                    await db.execute(
                        select(Appointment.id).where(Appointment.barber_id.in_(barber_ids))
                    )
                )
                .scalars()
                .all()
            )
            if appointment_ids:
                await db.execute(
                    delete(AppointmentReminder).where(
                        AppointmentReminder.appointment_id.in_(appointment_ids)
                    )
                )
            await db.execute(delete(Review).where(Review.barber_id.in_(barber_ids)))
            await db.execute(delete(Appointment).where(Appointment.barber_id.in_(barber_ids)))
            await db.execute(
                barber_services.delete().where(barber_services.c.barber_id.in_(barber_ids))
            )
            await db.execute(delete(WorkingHours).where(WorkingHours.barber_id.in_(barber_ids)))
            await db.execute(delete(Barber).where(Barber.shop_id == shop.id))
        await db.execute(delete(ShopClosure).where(ShopClosure.shop_id == shop.id))
        await db.execute(delete(Service).where(Service.shop_id == shop.id))
        await db.execute(delete(Barbershop).where(Barbershop.id == shop.id))
    await db.execute(delete(User).where(User.email.in_(emails)))
    await db.flush()


def _user(first: str, last: str, email: str, role: UserRole = UserRole.CUSTOMER) -> User:
    return User(
        first_name=first,
        last_name=last,
        email=email,
        phone="+7 700 000 0000",
        password_hash=hash_password(DEMO_PASSWORD),
        role=role,
    )


async def _history(
    db: AsyncSession, barbers: list[Barber], services: list[Service], customers: list[User]
) -> list[Appointment]:
    """A month behind and a fortnight ahead, with a realistic status mix."""
    now = datetime.now(UTC)
    created: list[Appointment] = []
    # Per barber, the spans already filled. Exact-start de-duplication is not
    # enough: a 45-minute cut at 12:15 overlaps a 60-minute one at 11:45, and
    # `no_double_booking` refuses the whole insert.
    booked: dict[str, list[tuple[datetime, datetime]]] = {str(b.id): [] for b in barbers}

    for day_offset in range(-30, 15):
        day = (now + timedelta(days=day_offset)).date()
        if day.weekday() == 6:  # closed Sundays
            continue

        for barber in barbers:
            for _ in range(random.randint(0, 4)):
                hour = random.choice([10, 11, 12, 14, 15, 16, 17])
                minute = random.choice([0, 15, 30, 45])
                # Working hours are Almaty time; Almaty is UTC+5.
                start = datetime(day.year, day.month, day.day, hour - 5, minute, tzinfo=UTC)

                service = random.choice(services)
                end = start + timedelta(minutes=service.duration_minutes)
                if any(start < e and s < end for s, e in booked[str(barber.id)]):
                    continue
                booked[str(barber.id)].append((start, end))
                status = _status(day_offset)
                appointment = Appointment(
                    customer_id=random.choice(customers).id,
                    barber_id=barber.id,
                    service_id=service.id,
                    start_time=start,
                    end_time=end,
                    total_price=service.price,
                    status=status,
                )
                if status is AppointmentStatus.CANCELLED:
                    appointment.cancellation_reason = "Customer could not make it"
                db.add(appointment)
                created.append(appointment)

    await db.flush()
    return created


def _status(day_offset: int) -> AppointmentStatus:
    if day_offset >= 0:
        return AppointmentStatus.CONFIRMED
    roll = random.random()
    if roll < 0.80:
        return AppointmentStatus.COMPLETED
    if roll < 0.92:
        return AppointmentStatus.CANCELLED
    return AppointmentStatus.NO_SHOW


async def _reviews(
    db: AsyncSession, appointments: list[Appointment], barbers: list[Barber]
) -> None:
    """Review roughly half the completed work, then set ratings to match."""
    tallies: dict[str, list[int]] = {str(b.id): [] for b in barbers}

    for appointment in appointments:
        if appointment.status is not AppointmentStatus.COMPLETED or random.random() > 0.5:
            continue
        rating = random.choices([5, 4, 3, 2], weights=[55, 30, 10, 5])[0]
        db.add(
            Review(
                customer_id=appointment.customer_id,
                barber_id=appointment.barber_id,
                appointment_id=appointment.id,
                rating=rating,
                comment=random.choice(COMMENTS),
            )
        )
        tallies[str(appointment.barber_id)].append(rating)

    for barber in barbers:
        scores = tallies[str(barber.id)]
        barber.reviews_count = len(scores)
        barber.rating = (
            Decimal(sum(scores) / len(scores)).quantize(Decimal("0.01")) if scores else Decimal("0")
        )
    await db.flush()


def _report(barbers: int, services: int, appointments: int) -> None:
    print(
        f"Seeded {SHOP_NAME}: {barbers} barbers, {services} services, {appointments} appointments"
    )
    print()
    print("Sign in with any of these (password: " + DEMO_PASSWORD + ")")
    print(f"  owner    {OWNER[2]}")
    for _, _, email, _, _ in BARBERS:
        print(f"  barber   {email}")
    print(f"  customer {CUSTOMERS[0][2]}")


if __name__ == "__main__":
    asyncio.run(seed())
