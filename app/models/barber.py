"""Barbers and their service assignments."""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.appointment import Appointment
    from app.models.barbershop import Barbershop
    from app.models.review import Review
    from app.models.schedule import BarberBreak, WorkingHours
    from app.models.service import Service
    from app.models.user import User


#: Many-to-many link. The rule that a service must belong to the barber's own
#: shop spans two tables, so a CHECK cannot express it — the service layer
#: enforces it (see docs/SPEC.md §3).
barber_services = Table(
    "barber_services",
    Base.metadata,
    Column(
        "barber_id",
        PGUUID(as_uuid=True),
        ForeignKey("barbers.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "service_id",
        PGUUID(as_uuid=True),
        ForeignKey("services.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Barber(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "barbers"
    __table_args__ = (
        UniqueConstraint("user_id", "shop_id", name="uq_barbers_user_id_shop_id"),
        CheckConstraint("experience_years >= 0", name="experience_years_non_negative"),
        CheckConstraint("rating >= 0 AND rating <= 5", name="rating_range"),
        CheckConstraint("reviews_count >= 0", name="reviews_count_non_negative"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shop_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("barbershops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bio: Mapped[str | None] = mapped_column(Text)
    experience_years: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Denormalized mean of `reviews.rating`, rewritten in the same transaction
    # as each review insert and repaired nightly (see docs/SPEC.md §11.3).
    rating: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    reviews_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    user: Mapped["User"] = relationship(back_populates="barber_profiles")
    shop: Mapped["Barbershop"] = relationship(back_populates="barbers")
    services: Mapped[list["Service"]] = relationship(
        secondary=barber_services, back_populates="barbers"
    )
    working_hours: Mapped[list["WorkingHours"]] = relationship(
        back_populates="barber", cascade="all, delete-orphan"
    )
    breaks: Mapped[list["BarberBreak"]] = relationship(
        back_populates="barber", cascade="all, delete-orphan"
    )
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="barber")
    reviews: Mapped[list["Review"]] = relationship(back_populates="barber")
