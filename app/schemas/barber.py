"""Barber request/response bodies."""

import uuid
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel
from app.schemas.service import ServiceRead


class BarberCreate(BaseModel):
    """Add an existing account to a shop as a barber.

    Identified by email rather than id: an owner knows their new hire's address,
    not their UUID. The account must already exist — this endpoint does not
    create users, so it cannot be used to farm accounts.
    """

    email: EmailStr
    bio: str | None = None
    experience_years: int = Field(default=0, ge=0, le=80)


class BarberUpdate(BaseModel):
    bio: str | None = None
    experience_years: int | None = Field(default=None, ge=0, le=80)
    is_active: bool | None = None


class BarberServicesUpdate(BaseModel):
    """Full replacement of the barber's service list."""

    service_ids: list[uuid.UUID] = Field(default_factory=list)


class BarberRead(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    shop_id: uuid.UUID
    first_name: str
    last_name: str
    bio: str | None
    experience_years: int
    rating: Decimal
    reviews_count: int
    is_active: bool
    services: list[ServiceRead] = Field(default_factory=list)
