"""Free-slot responses."""

import uuid
from datetime import date as date_type
from datetime import datetime

from pydantic import BaseModel


class SlotRead(BaseModel):
    start: datetime
    end: datetime
    #: The same instant in the shop's own timezone — what a customer actually
    #: reads off the page, so the client does not have to know the shop's tz.
    start_local: str
    end_local: str


class ServiceSummary(BaseModel):
    id: uuid.UUID
    name: str
    duration_minutes: int


class AvailabilityRead(BaseModel):
    date: date_type
    timezone: str
    barber_id: uuid.UUID
    service: ServiceSummary
    slots: list[SlotRead]
