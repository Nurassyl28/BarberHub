"""Query filters for the barbershop search."""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from fastapi import Query
from pydantic import BaseModel, Field, model_validator


class ShopSort(StrEnum):
    RATING_DESC = "-rating"
    RATING_ASC = "rating"
    NAME_ASC = "name"
    NAME_DESC = "-name"
    NEWEST = "-created_at"
    OLDEST = "created_at"
    DISTANCE = "distance"


class ShopFilters(BaseModel):
    city: Annotated[str | None, Query(description="Case-insensitive exact match")] = None
    q: Annotated[str | None, Query(description="Free text over name and description")] = None
    service: Annotated[
        str | None, Query(description="Shops offering an active service matching this name")
    ] = None
    min_rating: Annotated[Decimal | None, Query(ge=0, le=5)] = None
    lat: Annotated[Decimal | None, Query(ge=-90, le=90)] = None
    lng: Annotated[Decimal | None, Query(ge=-180, le=180)] = None
    radius_km: Annotated[Decimal | None, Query(gt=0, le=500)] = None
    sort: ShopSort = ShopSort.RATING_DESC

    @model_validator(mode="after")
    def coordinates_come_as_a_pair(self) -> Self:
        if (self.lat is None) != (self.lng is None):
            raise ValueError("lat and lng must be provided together")
        if self.radius_km is not None and self.lat is None:
            raise ValueError("radius_km requires lat and lng")
        if self.sort is ShopSort.DISTANCE and self.lat is None:
            raise ValueError("sort=distance requires lat and lng")
        return self

    @property
    def has_origin(self) -> bool:
        return self.lat is not None and self.lng is not None


class ShopSearchMeta(BaseModel):
    """Echoes back what the search actually applied."""

    sort: ShopSort
    applied: dict[str, str] = Field(default_factory=dict)
