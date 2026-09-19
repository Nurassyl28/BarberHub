"""Barbershops."""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.barber import Barber
    from app.models.closure import ShopClosure
    from app.models.service import Service
    from app.models.user import User


class Barbershop(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "barbershops"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    phone: Mapped[str | None] = mapped_column(String(32))
    # IANA name. Working hours are wall-clock; this is what turns them into
    # instants (see docs/SPEC.md §11.2).
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=settings.DEFAULT_TIMEZONE
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    #: When set, bookings arrive as PENDING and a member of staff must confirm
    #: them. The slot is held either way — a pending booking blocks the time,
    #: so a shop cannot double-sell it while deciding (see docs/SPEC.md §4).
    requires_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    owner: Mapped["User"] = relationship(back_populates="owned_shops")
    barbers: Mapped[list["Barber"]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    services: Mapped[list["Service"]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    closures: Mapped[list["ShopClosure"]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
