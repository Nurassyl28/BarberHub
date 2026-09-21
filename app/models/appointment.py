"""Appointments — and the constraint that makes double-booking impossible."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import ACTIVE_STATUS_SQL, AppointmentStatus

if TYPE_CHECKING:
    from app.models.barber import Barber
    from app.models.review import Review
    from app.models.service import Service
    from app.models.user import User


class Appointment(UUIDMixin, TimestampMixin, Base):
    """A booked slot.

    The `no_double_booking` exclusion constraint below is the authority on
    availability, not the application. Two transactions inserting overlapping
    ranges for the same barber cannot both commit: the loser raises SQLSTATE
    23P01, which the booking service maps to `409 SLOT_TAKEN`. A row lock on the
    barber would also work but would needlessly serialize bookings that do not
    overlap (see docs/SPEC.md §5).
    """

    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("end_time > start_time", name="end_after_start"),
        CheckConstraint("total_price >= 0", name="total_price_non_negative"),
        ExcludeConstraint(
            ("barber_id", "="),
            (func.tstzrange(text("start_time"), text("end_time"), text("'[)'")), "&&"),
            name="no_double_booking",
            using="gist",
            where=text(f"status IN ({ACTIVE_STATUS_SQL})"),
        ),
        Index("ix_appointments_barber_id_start_time", "barber_id", "start_time"),
        Index("ix_appointments_customer_id_start_time", "customer_id", text("start_time DESC")),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    barber_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("barbers.id", ondelete="RESTRICT"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus, name="appointment_status", native_enum=True),
        nullable=False,
        default=AppointmentStatus.CONFIRMED,
        server_default=AppointmentStatus.CONFIRMED.value,
    )
    #: Snapshot of the service price at booking time — a later price change must
    #: not rewrite history or the revenue figures.
    total_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    cancelled_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(255))

    customer: Mapped["User"] = relationship(
        back_populates="appointments", foreign_keys=[customer_id]
    )
    cancelled_by: Mapped["User | None"] = relationship(foreign_keys=[cancelled_by_id])
    barber: Mapped["Barber"] = relationship(back_populates="appointments")
    service: Mapped["Service"] = relationship()
    review: Mapped["Review | None"] = relationship(
        back_populates="appointment", cascade="all, delete-orphan", uselist=False
    )
