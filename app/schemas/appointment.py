"""Appointment request/response bodies."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import AppointmentStatus
from app.schemas.availability import ServiceSummary
from app.schemas.common import ORMModel


def _require_aware(value: datetime) -> datetime:
    # Same rule as breaks: guessing a timezone would put the booking hours away
    # from where the customer meant it (see docs/SPEC.md §11.2).
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "start_time must include a timezone offset, e.g. 2026-09-20T13:00:00+05:00"
        )
    return value


class AppointmentCreate(BaseModel):
    barber_id: uuid.UUID
    service_id: uuid.UUID
    start_time: datetime

    _aware = field_validator("start_time")(_require_aware)


class AppointmentReschedule(BaseModel):
    start_time: datetime

    _aware = field_validator("start_time")(_require_aware)


class AppointmentCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class PersonSummary(BaseModel):
    id: uuid.UUID
    first_name: str
    last_name: str


class AppointmentRead(ORMModel):
    id: uuid.UUID
    customer: PersonSummary
    barber_id: uuid.UUID
    barber: PersonSummary
    shop_id: uuid.UUID
    service: ServiceSummary
    start_time: datetime
    end_time: datetime
    status: AppointmentStatus
    total_price: Decimal
    cancellation_reason: str | None = None
    created_at: datetime


class AppointmentFilters(BaseModel):
    """Shared query filters for the two listing endpoints."""

    status: AppointmentStatus | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    barber_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def range_is_ordered(self) -> Self:
        if self.date_from and self.date_to and self.date_to <= self.date_from:
            raise ValueError("date_to must be after date_from")
        return self
