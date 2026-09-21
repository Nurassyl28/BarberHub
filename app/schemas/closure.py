"""Shop closure request/response bodies."""

import uuid
from datetime import date as date_type
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel


class ClosureCreate(BaseModel):
    start_date: date_type
    #: Inclusive. A single closed day has `end_date == start_date`.
    end_date: date_type
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def range_is_ordered(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class ClosureRead(ORMModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    start_date: date_type
    end_date: date_type
    reason: str | None
