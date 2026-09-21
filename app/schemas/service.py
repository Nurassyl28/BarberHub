"""Service request/response bodies."""

import uuid
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

#: Bounds mirror the `duration_minutes_range` CHECK so a bad value is a 422 at
#: the edge rather than a 500 from the database.
Duration = Annotated[int, Field(gt=0, le=480)]
Price = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]


class ServiceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    description: str | None = None
    duration_minutes: Duration
    price: Price


class ServiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = None
    duration_minutes: Duration | None = None
    price: Price | None = None
    is_active: bool | None = None


class ServiceRead(ORMModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    name: str
    description: str | None
    duration_minutes: int
    price: Decimal
    is_active: bool
