"""Weekly working hours and one-off breaks."""

import uuid
from datetime import datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.barber import Barber


class WorkingHours(UUIDMixin, TimestampMixin, Base):
    """One shift on one weekday, in the shop's local wall clock.

    Several rows per weekday are allowed, which is how split shifts
    (09:00–13:00 and 14:00–19:00) are expressed.
    """

    __tablename__ = "working_hours"
    __table_args__ = (
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="weekday_range"),
        CheckConstraint("end_time > start_time", name="end_after_start"),
        UniqueConstraint("barber_id", "weekday", "start_time", name="uq_working_hours_barber_day"),
    )

    barber_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("barbers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: ISO weekday, 0 = Monday .. 6 = Sunday.
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    barber: Mapped["Barber"] = relationship(back_populates="working_hours")


class BarberBreak(UUIDMixin, TimestampMixin, Base):
    """An absolute-time block: lunch on a given day, vacation, an outside job."""

    __tablename__ = "barber_breaks"
    __table_args__ = (
        CheckConstraint("end_datetime > start_datetime", name="end_after_start"),
        Index("ix_barber_breaks_barber_id_start", "barber_id", "start_datetime"),
    )

    barber_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("barbers.id", ondelete="CASCADE"), nullable=False
    )
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))

    barber: Mapped["Barber"] = relationship(back_populates="breaks")
