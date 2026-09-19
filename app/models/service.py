"""Services a shop offers."""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.barber import Barber
    from app.models.barbershop import Barbershop


class Service(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint(
            "duration_minutes > 0 AND duration_minutes <= 480", name="duration_minutes_range"
        ),
        CheckConstraint("price >= 0", name="price_non_negative"),
        # Case-insensitive uniqueness per shop: "Haircut" and "haircut" collide.
        Index(
            "uq_services_shop_id_name_lower",
            "shop_id",
            text("lower(name)"),
            unique=True,
        ),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("barbershops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    shop: Mapped["Barbershop"] = relationship(back_populates="services")
    barbers: Mapped[list["Barber"]] = relationship(
        secondary="barber_services", back_populates="services"
    )
