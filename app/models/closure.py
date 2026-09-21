"""Shop-wide closures: public holidays, refits, the owner's holiday."""

import uuid
from datetime import date as date_type
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.barbershop import Barbershop


class ShopClosure(UUIDMixin, TimestampMixin, Base):
    """A range of local calendar days on which the whole shop is shut.

    Distinct from a `barber_break`, which blocks one person's time. A public
    holiday closes everyone at once, and expressing that as a break per barber
    would mean N rows that can drift apart — remove one barber's and the shop is
    half-open on New Year's Day.

    Stored as local dates rather than instants: "closed on 1 January" is a
    statement about the shop's calendar, not about a 24-hour span of UTC.
    """

    __tablename__ = "shop_closures"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="end_not_before_start"),
        UniqueConstraint("shop_id", "start_date", "end_date", name="uq_shop_closures_range"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("barbershops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    #: Inclusive — a one-day closure has start_date == end_date.
    end_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))

    shop: Mapped["Barbershop"] = relationship(back_populates="closures")
