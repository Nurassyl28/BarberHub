"""Payload-level rules for working hours and breaks — no DB involved."""

from datetime import UTC, datetime, time, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.schedule import BreakCreate, WorkingHoursSlot, WorkingHoursUpdate


def slot(weekday: int, start: str, end: str) -> dict[str, object]:
    return {"weekday": weekday, "start_time": start, "end_time": end}


class TestWorkingHoursSlot:
    def test_accepts_a_normal_shift(self) -> None:
        parsed = WorkingHoursSlot(weekday=0, start_time=time(9), end_time=time(18))

        assert parsed.weekday == 0

    def test_end_before_start_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="end_time must be after start_time"):
            WorkingHoursSlot(weekday=0, start_time=time(18), end_time=time(9))

    def test_zero_length_shift_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkingHoursSlot(weekday=0, start_time=time(9), end_time=time(9))

    @pytest.mark.parametrize("weekday", [-1, 7, 99])
    def test_weekday_is_bounded(self, weekday: int) -> None:
        with pytest.raises(ValidationError):
            WorkingHoursSlot(weekday=weekday, start_time=time(9), end_time=time(18))


class TestWorkingHoursUpdate:
    def test_empty_week_is_valid(self) -> None:
        """Sending nothing is how a barber clears their schedule."""
        assert WorkingHoursUpdate(items=[]).items == []

    def test_split_shift_on_one_day_is_allowed(self) -> None:
        parsed = WorkingHoursUpdate.model_validate(
            {"items": [slot(0, "09:00", "13:00"), slot(0, "14:00", "19:00")]}
        )

        assert len(parsed.items) == 2

    def test_back_to_back_shifts_are_allowed(self) -> None:
        """13:00-14:00 followed by 14:00-18:00 touch but do not overlap."""
        parsed = WorkingHoursUpdate.model_validate(
            {"items": [slot(0, "13:00", "14:00"), slot(0, "14:00", "18:00")]}
        )

        assert len(parsed.items) == 2

    def test_overlapping_shifts_on_one_day_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Overlapping shifts on weekday 0"):
            WorkingHoursUpdate.model_validate(
                {"items": [slot(0, "09:00", "15:00"), slot(0, "14:00", "19:00")]}
            )

    def test_overlap_is_detected_regardless_of_input_order(self) -> None:
        with pytest.raises(ValidationError, match="Overlapping shifts"):
            WorkingHoursUpdate.model_validate(
                {"items": [slot(0, "14:00", "19:00"), slot(0, "09:00", "15:00")]}
            )

    def test_exact_duplicates_are_rejected(self) -> None:
        """Duplicates would otherwise hit the (barber, weekday, start) unique index."""
        with pytest.raises(ValidationError, match="Overlapping shifts"):
            WorkingHoursUpdate.model_validate(
                {"items": [slot(0, "09:00", "18:00"), slot(0, "09:00", "18:00")]}
            )

    def test_same_hours_on_different_days_are_fine(self) -> None:
        parsed = WorkingHoursUpdate.model_validate(
            {"items": [slot(d, "09:00", "18:00") for d in range(7)]}
        )

        assert len(parsed.items) == 7

    def test_a_shift_fully_inside_another_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Overlapping shifts"):
            WorkingHoursUpdate.model_validate(
                {"items": [slot(3, "09:00", "18:00"), slot(3, "10:00", "11:00")]}
            )


class TestBreakCreate:
    def test_accepts_an_aware_range(self) -> None:
        start = datetime(2026, 9, 20, 9, tzinfo=UTC)

        parsed = BreakCreate(
            start_datetime=start, end_datetime=start + timedelta(hours=1), reason="Lunch"
        )

        assert parsed.reason == "Lunch"

    def test_naive_start_is_rejected(self) -> None:
        """Guessing UTC or shop-local would be wrong half the time (§11.2)."""
        with pytest.raises(ValidationError, match="must include a timezone offset"):
            BreakCreate.model_validate(
                {
                    "start_datetime": "2026-09-20T09:00:00",
                    "end_datetime": "2026-09-20T10:00:00+00:00",
                }
            )

    def test_naive_end_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must include a timezone offset"):
            BreakCreate.model_validate(
                {
                    "start_datetime": "2026-09-20T09:00:00+00:00",
                    "end_datetime": "2026-09-20T10:00:00",
                }
            )

    def test_end_before_start_is_rejected(self) -> None:
        start = datetime(2026, 9, 20, 9, tzinfo=UTC)

        with pytest.raises(ValidationError, match="end_datetime must be after start_datetime"):
            BreakCreate(start_datetime=start, end_datetime=start - timedelta(hours=1))

    def test_non_utc_offsets_are_accepted(self) -> None:
        parsed = BreakCreate.model_validate(
            {
                "start_datetime": "2026-09-20T13:00:00+05:00",
                "end_datetime": "2026-09-20T14:00:00+05:00",
            }
        )

        assert parsed.start_datetime.utcoffset() == timedelta(hours=5)
