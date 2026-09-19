"""Bounding-box maths — pure, no database."""

import math
from decimal import Decimal

from app.services.geo import KM_PER_DEGREE_LAT, bounding_box

ALMATY = (Decimal("43.238949"), Decimal("76.889709"))


class TestBoundingBox:
    def test_latitude_span_matches_the_radius(self) -> None:
        lat_min, lat_max, _, _ = bounding_box(*ALMATY, Decimal("10"))

        span_km = (lat_max - lat_min) * KM_PER_DEGREE_LAT

        assert math.isclose(span_km, 20, rel_tol=0.01)

    def test_longitude_span_widens_with_latitude(self) -> None:
        """A kilometre buys more degrees of longitude the further from the equator."""
        _, _, equator_min, equator_max = bounding_box(Decimal("0"), Decimal("0"), Decimal("10"))
        _, _, north_min, north_max = bounding_box(Decimal("60"), Decimal("0"), Decimal("10"))

        assert (north_max - north_min) > (equator_max - equator_min)

    def test_the_box_contains_its_own_centre(self) -> None:
        lat_min, lat_max, lng_min, lng_max = bounding_box(*ALMATY, Decimal("5"))

        assert lat_min < float(ALMATY[0]) < lat_max
        assert lng_min < float(ALMATY[1]) < lng_max

    def test_latitude_is_clamped_to_the_poles(self) -> None:
        lat_min, lat_max, _, _ = bounding_box(Decimal("89"), Decimal("0"), Decimal("500"))

        assert lat_min >= -90.0
        assert lat_max <= 90.0

    def test_near_the_pole_the_longitude_span_stays_finite(self) -> None:
        """Meridians converge to a point at the pole; without a floor this divides by ~0."""
        _, _, lng_min, lng_max = bounding_box(Decimal("90"), Decimal("0"), Decimal("10"))

        assert math.isfinite(lng_min) and math.isfinite(lng_max)
        assert (lng_max - lng_min) < 1000

    def test_a_bigger_radius_gives_a_bigger_box(self) -> None:
        small = bounding_box(*ALMATY, Decimal("1"))
        large = bounding_box(*ALMATY, Decimal("50"))

        assert (large[1] - large[0]) > (small[1] - small[0])
