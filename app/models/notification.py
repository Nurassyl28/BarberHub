"""Record of notifications already sent."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.appointment import Appointment


class ReminderKind(StrEnum):
    DAY_BEFORE = "DAY_BEFORE"
    HOURS_BEFORE = "HOURS_BEFORE"


class AppointmentReminder(UUIDMixin, Base):
    """One row per reminder actually sent.

    Idempotency lives in the unique constraint, not in a "was it sent recently"
    time-window check. The beat schedule can fire twice, two workers can pick up
    the same appointment, a deploy can replay a batch — and the customer still
    gets exactly one message, because the second insert simply fails
    (see docs/SPEC.md §9).
    """

    __tablename__ = "appointment_reminders"
    __table_args__ = (
        UniqueConstraint("appointment_id", "kind", name="uq_appointment_reminders_once"),
    )

    appointment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[ReminderKind] = mapped_column(
        Enum(ReminderKind, name="reminder_kind", native_enum=True), nullable=False
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    appointment: Mapped["Appointment"] = relationship()
