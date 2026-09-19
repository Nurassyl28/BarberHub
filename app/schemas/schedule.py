"""Working hours and breaks."""

import uuid
from datetime import datetime, time
from itertools import pairwise
from typing import Annotated, Self

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel

#: ISO weekday, 0 = Monday .. 6 = Sunday.
Weekday = Annotated[int, Field(ge=0, le=6)]


class WorkingHoursSlot(BaseModel):
    """One shift, in the shop's local wall clock — never UTC.

    "Monday 09:00" means 09:00 where the shop is; the availability engine is
    what turns that into instants using `barbershops.timezone` (§5).
    """

    weekday: Weekday
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def end_is_after_start(self) -> Self:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class WorkingHoursUpdate(BaseModel):
    """The barber's whole week, replaced in one request.

    A full replace rather than per-row edits: the weekly schedule is one thing
    a barber thinks about as a whole, and it makes "delete Tuesday" expressible
    without a separate endpoint.
    """

    items: list[WorkingHoursSlot] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def shifts_do_not_overlap(self) -> Self:
        by_day: dict[int, list[WorkingHoursSlot]] = {}
        for slot in self.items:
            by_day.setdefault(slot.weekday, []).append(slot)

        for weekday, slots in by_day.items():
            ordered = sorted(slots, key=lambda s: s.start_time)
            for earlier, later in pairwise(ordered):
                if later.start_time < earlier.end_time:
                    raise ValueError(
                        f"Overlapping shifts on weekday {weekday}: "
                        f"{earlier.start_time}-{earlier.end_time} and "
                        f"{later.start_time}-{later.end_time}"
                    )
        return self


class WorkingHoursRead(ORMModel):
    id: uuid.UUID
    barber_id: uuid.UUID
    weekday: Weekday
    start_time: time
    end_time: time


class BreakCreate(BaseModel):
    """An absolute-time block: lunch on a given day, vacation, an outside job."""

    start_datetime: datetime
    end_datetime: datetime
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def is_a_valid_future_range(self) -> Self:
        for label, value in (
            ("start_datetime", self.start_datetime),
            ("end_datetime", self.end_datetime),
        ):
            # Naive input is refused rather than guessed at: assuming UTC or
            # assuming shop-local are both wrong half the time, and the mistake
            # would surface as a break silently landing hours away.
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(
                    f"{label} must include a timezone offset, e.g. 2026-09-20T13:00:00+05:00"
                )
        if self.end_datetime <= self.start_datetime:
            raise ValueError("end_datetime must be after start_datetime")
        return self


class BreakRead(ORMModel):
    id: uuid.UUID
    barber_id: uuid.UUID
    start_datetime: datetime
    end_datetime: datetime
    reason: str | None
