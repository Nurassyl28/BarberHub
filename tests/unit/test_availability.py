"""The interval arithmetic behind free slots (docs/SPEC.md §5, §10).

No database, no HTTP — just values in, values out.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.availability import (
    Interval,
    align_up,
    clip,
    compute_free_slots,
    local_day_bounds,
    normalize,
    slots_in,
    subtract,
    working_interval,
)

ALMATY = ZoneInfo("Asia/Almaty")
BERLIN = ZoneInfo("Europe/Berlin")
DAY = date(2026, 9, 21)  # a Monday

STEP = timedelta(minutes=15)
FORTY_FIVE = timedelta(minutes=45)


def utc(hour: int, minute: int = 0, *, day: int = 21) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def span(start_hour: float, end_hour: float) -> Interval:
    def to_dt(value: float) -> datetime:
        return utc(int(value), round((value % 1) * 60))

    return Interval(to_dt(start_hour), to_dt(end_hour))


class TestInterval:
    def test_rejects_naive_bounds(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            Interval(datetime(2026, 9, 21, 9), utc(10))

    def test_rejects_an_inverted_range(self) -> None:
        with pytest.raises(ValueError, match="end must be after start"):
            Interval(utc(10), utc(9))

    def test_rejects_a_zero_length_range(self) -> None:
        with pytest.raises(ValueError):
            Interval(utc(10), utc(10))

    def test_touching_intervals_do_not_overlap(self) -> None:
        """Half-open bounds: 09:00-10:00 and 10:00-11:00 are adjacent, not clashing."""
        assert not span(9, 10).overlaps(span(10, 11))

    def test_overlapping_intervals_are_detected_both_ways(self) -> None:
        a, b = span(9, 11), span(10, 12)

        assert a.overlaps(b) and b.overlaps(a)


class TestNormalize:
    def test_empty_input(self) -> None:
        assert normalize([]) == []

    def test_sorts_out_of_order_input(self) -> None:
        assert normalize([span(14, 15), span(9, 10)]) == [span(9, 10), span(14, 15)]

    def test_merges_overlapping(self) -> None:
        assert normalize([span(9, 11), span(10, 12)]) == [span(9, 12)]

    def test_merges_touching(self) -> None:
        assert normalize([span(9, 10), span(10, 11)]) == [span(9, 11)]

    def test_keeps_a_real_gap(self) -> None:
        assert normalize([span(9, 10), span(11, 12)]) == [span(9, 10), span(11, 12)]

    def test_swallows_a_contained_interval(self) -> None:
        assert normalize([span(9, 18), span(12, 13)]) == [span(9, 18)]


class TestSubtract:
    def test_no_blocks_returns_the_base(self) -> None:
        assert subtract([span(9, 18)], []) == [span(9, 18)]

    def test_block_in_the_middle_splits_the_day(self) -> None:
        assert subtract([span(9, 18)], [span(13, 14)]) == [span(9, 13), span(14, 18)]

    def test_block_at_the_start_trims_it(self) -> None:
        assert subtract([span(9, 18)], [span(9, 10)]) == [span(10, 18)]

    def test_block_at_the_end_trims_it(self) -> None:
        assert subtract([span(9, 18)], [span(17, 18)]) == [span(9, 17)]

    def test_block_covering_everything_leaves_nothing(self) -> None:
        assert subtract([span(9, 18)], [span(8, 20)]) == []

    def test_block_outside_the_day_changes_nothing(self) -> None:
        assert subtract([span(9, 18)], [span(19, 20)]) == [span(9, 18)]

    def test_touching_block_changes_nothing(self) -> None:
        """A break starting exactly when work ends removes no working time."""
        assert subtract([span(9, 18)], [span(18, 19)]) == [span(9, 18)]

    def test_several_blocks_produce_several_gaps(self) -> None:
        result = subtract([span(9, 18)], [span(11, 12), span(15, 16)])

        assert result == [span(9, 11), span(12, 15), span(16, 18)]

    def test_overlapping_blocks_are_merged_first(self) -> None:
        result = subtract([span(9, 18)], [span(11, 13), span(12, 14)])

        assert result == [span(9, 11), span(14, 18)]

    def test_blocks_apply_across_split_shifts(self) -> None:
        result = subtract([span(9, 13), span(14, 19)], [span(12, 15)])

        assert result == [span(9, 12), span(15, 19)]

    def test_unsorted_blocks_are_handled(self) -> None:
        assert subtract([span(9, 18)], [span(15, 16), span(11, 12)]) == [
            span(9, 11),
            span(12, 15),
            span(16, 18),
        ]


class TestAlignUp:
    def test_a_moment_already_on_the_grid_is_unchanged(self) -> None:
        assert align_up(utc(9), STEP, utc(0)) == utc(9)

    def test_rounds_up_to_the_next_step(self) -> None:
        assert align_up(utc(9, 7), STEP, utc(0)) == utc(9, 15)

    def test_one_second_past_the_grid_still_rounds_up(self) -> None:
        moment = utc(9) + timedelta(seconds=1)

        assert align_up(moment, STEP, utc(0)) == utc(9, 15)

    def test_the_anchor_shifts_the_whole_grid(self) -> None:
        assert align_up(utc(9, 1), STEP, utc(0, 10)) == utc(9, 10)


class TestSlotsIn:
    def test_a_simple_morning(self) -> None:
        slots = slots_in(
            [span(9, 10)],
            duration=timedelta(minutes=30),
            step=timedelta(minutes=30),
            not_before=utc(0),
            anchor=utc(0),
        )

        assert [s.start for s in slots] == [utc(9), utc(9, 30)]

    def test_slots_overlap_when_the_step_is_shorter_than_the_service(self) -> None:
        """A 15-minute grid with a 45-minute cut offers rolling start times."""
        slots = slots_in(
            [span(9, 10)],
            duration=FORTY_FIVE,
            step=STEP,
            not_before=utc(0),
            anchor=utc(0),
        )

        assert [s.start for s in slots] == [utc(9), utc(9, 15)]

    def test_a_service_longer_than_the_gap_yields_nothing(self) -> None:
        slots = slots_in(
            [span(9, 9.5)],
            duration=FORTY_FIVE,
            step=STEP,
            not_before=utc(0),
            anchor=utc(0),
        )

        assert slots == []

    def test_a_service_exactly_filling_the_gap_yields_one_slot(self) -> None:
        slots = slots_in(
            [span(9, 9.75)],
            duration=FORTY_FIVE,
            step=STEP,
            not_before=utc(0),
            anchor=utc(0),
        )

        assert [s.start for s in slots] == [utc(9)]

    def test_starts_stay_on_the_grid_after_an_odd_gap(self) -> None:
        """A gap opening at 09:07 must still offer 09:15, not 09:07."""
        odd = Interval(utc(9, 7), utc(11))

        slots = slots_in([odd], duration=FORTY_FIVE, step=STEP, not_before=utc(0), anchor=utc(0))

        assert slots[0].start == utc(9, 15)

    def test_lead_time_hides_slots_that_are_too_soon(self) -> None:
        slots = slots_in(
            [span(9, 12)],
            duration=FORTY_FIVE,
            step=STEP,
            not_before=utc(10, 5),
            anchor=utc(0),
        )

        assert slots[0].start == utc(10, 15)

    def test_lead_time_past_the_end_hides_everything(self) -> None:
        slots = slots_in(
            [span(9, 12)],
            duration=FORTY_FIVE,
            step=STEP,
            not_before=utc(23),
            anchor=utc(0),
        )

        assert slots == []

    @pytest.mark.parametrize("bad", [timedelta(0), timedelta(minutes=-5)])
    def test_non_positive_duration_is_refused(self, bad: timedelta) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            slots_in([span(9, 12)], duration=bad, step=STEP, not_before=utc(0), anchor=utc(0))

    def test_non_positive_step_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            slots_in(
                [span(9, 12)],
                duration=FORTY_FIVE,
                step=timedelta(0),
                not_before=utc(0),
                anchor=utc(0),
            )


class TestClip:
    def test_trims_an_interval_that_pokes_out(self) -> None:
        window = span(9, 18)

        assert clip([span(8, 10)], window) == [span(9, 10)]

    def test_drops_an_interval_entirely_outside(self) -> None:
        assert clip([span(20, 21)], span(9, 18)) == []

    def test_drops_an_interval_merely_touching_the_window(self) -> None:
        assert clip([span(18, 19)], span(9, 18)) == []


class TestTimezoneBoundary:
    def test_a_local_day_is_twenty_four_hours_normally(self) -> None:
        bounds = local_day_bounds(DAY, ALMATY)

        assert bounds.duration == timedelta(hours=24)

    def test_almaty_midnight_is_the_previous_day_in_utc(self) -> None:
        bounds = local_day_bounds(DAY, ALMATY)

        assert bounds.start.astimezone(UTC) == datetime(2026, 9, 20, 19, tzinfo=UTC)

    def test_a_shift_becomes_the_right_instants(self) -> None:
        shift = working_interval(DAY, time(9), time(18), ALMATY)

        assert shift is not None
        assert shift.start.astimezone(UTC) == datetime(2026, 9, 21, 4, tzinfo=UTC)
        assert shift.duration == timedelta(hours=9)

    def test_an_inverted_shift_is_rejected(self) -> None:
        assert working_interval(DAY, time(18), time(9), ALMATY) is None


class TestDaylightSaving:
    """Berlin springs forward 2026-03-29 and falls back 2026-10-25."""

    SPRING = date(2026, 3, 29)
    AUTUMN = date(2026, 10, 25)

    def test_the_spring_day_is_only_twenty_three_hours(self) -> None:
        assert local_day_bounds(self.SPRING, BERLIN).duration == timedelta(hours=23)

    def test_the_autumn_day_is_twenty_five_hours(self) -> None:
        assert local_day_bounds(self.AUTUMN, BERLIN).duration == timedelta(hours=25)

    def test_a_shift_across_spring_forward_loses_an_hour_of_real_time(self) -> None:
        """00:00-06:00 nominal is five real hours when 02:00 never happens."""
        shift = working_interval(self.SPRING, time(0), time(6), BERLIN)

        assert shift is not None
        assert shift.duration == timedelta(hours=5)

    def test_a_shift_across_fall_back_gains_an_hour_of_real_time(self) -> None:
        shift = working_interval(self.AUTUMN, time(0), time(6), BERLIN)

        assert shift is not None
        assert shift.duration == timedelta(hours=7)

    def test_slot_count_follows_real_time_not_wall_clock(self) -> None:
        """The same nominal shift yields fewer slots on the short day."""
        normal = working_interval(date(2026, 3, 22), time(0), time(6), BERLIN)
        short = working_interval(self.SPRING, time(0), time(6), BERLIN)
        assert normal is not None and short is not None

        def count(shift: Interval) -> int:
            return len(
                slots_in(
                    [shift],
                    duration=timedelta(hours=1),
                    step=timedelta(hours=1),
                    not_before=shift.start,
                    anchor=shift.start,
                )
            )

        assert count(normal) == 6
        assert count(short) == 5


class TestComputeFreeSlots:
    """End-to-end over the pure layer: a realistic day."""

    def _call(self, working: list[Interval], busy: list[Interval]) -> list[datetime]:
        return [
            s.start
            for s in compute_free_slots(
                working=working,
                busy=busy,
                duration=FORTY_FIVE,
                step=STEP,
                not_before=utc(0),
                anchor=utc(0),
            )
        ]

    def test_no_working_hours_means_no_slots(self) -> None:
        assert self._call([], []) == []

    def test_a_free_morning(self) -> None:
        assert self._call([span(9, 10.5)], []) == [utc(9), utc(9, 15), utc(9, 30), utc(9, 45)]

    def test_one_appointment_carves_out_its_time(self) -> None:
        """The 30-minute gap left before the booking cannot hold a 45-minute cut."""
        starts = self._call([span(9, 11)], [span(9.5, 10.25)])

        assert starts == [utc(10, 15)]

    def test_a_break_exactly_on_a_slot_boundary_removes_only_that_slot(self) -> None:
        """A 09:30-10:00 break removes the 09:30 slot and leaves its neighbours."""
        starts = [
            s.start
            for s in compute_free_slots(
                working=[span(9, 10.5)],
                busy=[span(9.5, 10)],
                duration=timedelta(minutes=30),
                step=timedelta(minutes=30),
                not_before=utc(0),
                anchor=utc(0),
            )
        ]

        assert starts == [utc(9), utc(10)]

    def test_appointments_and_breaks_are_treated_identically(self) -> None:
        both = self._call([span(9, 12)], [span(10, 10.75), span(11, 11.75)])
        one_list = self._call([span(9, 12)], [span(10, 10.75)])

        assert utc(10) not in both
        assert utc(11) not in both
        assert utc(11) in one_list

    def test_a_split_shift_offers_slots_in_both_halves(self) -> None:
        starts = self._call([span(9, 10), span(14, 15)], [])

        assert starts == [utc(9), utc(9, 15), utc(14), utc(14, 15)]

    def test_a_day_fully_booked_yields_nothing(self) -> None:
        assert self._call([span(9, 18)], [span(8, 19)]) == []

    def test_blocks_outside_working_hours_are_harmless(self) -> None:
        assert self._call([span(9, 9.75)], [span(20, 21)]) == [utc(9)]
