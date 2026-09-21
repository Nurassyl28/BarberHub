"""Distance maths for the "near me" filter."""

import math
from decimal import Decimal
from typing import Any

from sqlalchemy import Float, func, literal
from sqlalchemy.sql import ColumnElement

#: Mean Earth radius. Good to ~0.3% for city-scale distances, which is far
#: finer than a street address is anyway.
EARTH_RADIUS_KM = 6371.0

#: One degree of latitude, everywhere.
KM_PER_DEGREE_LAT = 111.045


def haversine_km(
    lat_column: Any,
    lng_column: Any,
    lat: Decimal,
    lng: Decimal,
) -> ColumnElement[float]:
    """Great-circle distance in kilometres, as a SQL expression.

    The argument to `acos` is clamped to [-1, 1]. Floating-point rounding can
    push it a hair outside that range for two points at (or extremely near) the
    same spot, and PostgreSQL answers that with a hard `input is out of range`
    error rather than a NaN — so a shop finding itself would fail the query.
    """
    origin_lat = func.radians(literal(float(lat)))
    origin_lng = func.radians(literal(float(lng)))
    target_lat = func.radians(lat_column.cast(Float))
    target_lng = func.radians(lng_column.cast(Float))

    cosine = func.cos(origin_lat) * func.cos(target_lat) * func.cos(
        target_lng - origin_lng
    ) + func.sin(origin_lat) * func.sin(target_lat)

    clamped = func.least(literal(1.0), func.greatest(literal(-1.0), cosine))
    return literal(EARTH_RADIUS_KM) * func.acos(clamped)


def bounding_box(
    lat: Decimal, lng: Decimal, radius_km: Decimal
) -> tuple[float, float, float, float]:
    """A crude lat/lng box enclosing the radius.

    Used as a prefilter so a plain btree index on the coordinates can throw away
    most rows before the expensive trigonometry runs on the survivors.
    """
    lat_f, lng_f, radius = float(lat), float(lng), float(radius_km)
    lat_delta = radius / KM_PER_DEGREE_LAT

    # Meridians converge toward the poles, so a kilometre is worth more degrees
    # of longitude the further north you go. The floor keeps this finite near
    # the poles instead of dividing by ~0.
    shrink = max(math.cos(math.radians(lat_f)), 0.01)
    lng_delta = radius / (KM_PER_DEGREE_LAT * shrink)

    return (
        max(lat_f - lat_delta, -90.0),
        min(lat_f + lat_delta, 90.0),
        lng_f - lng_delta,
        lng_f + lng_delta,
    )
