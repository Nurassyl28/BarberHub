"""Review request/response bodies."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

Rating = Annotated[int, Field(ge=1, le=5)]


class ReviewCreate(BaseModel):
    rating: Rating
    comment: str | None = Field(default=None, max_length=2000)


class ReviewRead(ORMModel):
    id: uuid.UUID
    barber_id: uuid.UUID
    appointment_id: uuid.UUID
    rating: int
    comment: str | None
    #: "Dana T." — reviews are public, and a full name plus a dated haircut is
    #: more than a customer signed up to publish.
    author: str
    created_at: datetime


class BarberRatingRead(BaseModel):
    barber_id: uuid.UUID
    rating: Decimal
    reviews_count: int
