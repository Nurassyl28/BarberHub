"""Barbershop request/response bodies."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.schemas.common import ORMModel

Latitude = Annotated[Decimal, Field(ge=-90, le=90)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180)]


def _validate_timezone(value: str) -> str:
    """Reject anything `zoneinfo` cannot load.

    A bad timezone here would not fail until the availability engine tried to
    turn working hours into instants, which is far from where the mistake was
    made (see docs/SPEC.md §11.2).
    """
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown IANA timezone: {value!r}") from exc
    return value


class BarbershopBase(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    description: str | None = None
    address: str = Field(min_length=2, max_length=255)
    city: str = Field(min_length=1, max_length=100)
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    phone: str | None = Field(default=None, max_length=32)
    timezone: str = Field(default=settings.DEFAULT_TIMEZONE, max_length=64)

    _check_timezone = field_validator("timezone")(_validate_timezone)


class BarbershopCreate(BarbershopBase):
    pass


class BarbershopUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = None
    address: str | None = Field(default=None, min_length=2, max_length=255)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    phone: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None

    @field_validator("timezone")
    @classmethod
    def timezone_is_known(cls, value: str | None) -> str | None:
        return None if value is None else _validate_timezone(value)


class BarbershopRead(ORMModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    description: str | None
    address: str
    city: str
    latitude: Decimal | None
    longitude: Decimal | None
    phone: str | None
    timezone: str
    is_active: bool
    created_at: datetime
    #: Mean rating across the shop's *rated* barbers — barbers with no reviews
    #: are excluded rather than counted as zero.
    rating: Decimal = Decimal("0")
    reviews_count: int = 0
    barbers_count: int = 0
    #: Only populated when the search supplied `lat` and `lng`.
    distance_km: float | None = None
