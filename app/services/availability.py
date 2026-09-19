"""Interval arithmetic behind free-slot computation.

Deliberately free of the database, FastAPI, and application settings: every
function here takes plain values and returns plain values, so the hard part of
this project — the arithmetic — can be tested exhaustively without fixtures
(see docs/SPEC.md §5).

Every interval is half-open, `[start, end)`. That is the same convention as the
`no_double_booking` exclusion constraint, which is why a 10:00-10:45 cut and a
10:45-11:30 cut do not collide.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from datetime import date as date_type
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True, order=True)
class Interval:
    """A half-open span of time, always stored as UTC instants.

    Bounds are converted to UTC on construction, and that is not cosmetic.
    Python subtracts two aware datetimes that share a `tzinfo` object by wall
    clock, ignoring the offset — so on a DST day `midnight - midnight` reports
    24 hours when only 23 elapsed. Every comparison and every `+ duration` in
    this module would inherit that error. Normalizing here means the arithmetic
    below is real elapsed time by construction; wall-clock values exist only at
    the boundary, in `working_interval` and when rendering a response.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Interval bounds must be timezone-aware")
        object.__setattr__(self, "start", self.start.astimezone(UTC))
        object.__setattr__(self, "end", self.end.astimezone(UTC))
        if self.end <= self.start:
            raise ValueError(f"Interval end must be after start: {self.start} .. {self.end}")

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end


def normalize(intervals: Iterable[Interval]) -> list[Interval]:
    """Sort and merge, collapsing overlapping *and* touching spans into one.

    Touching spans are merged too: 09:00-10:00 and 10:00-11:00 block exactly the
    same time as 09:00-11:00, and leaving them separate would let a zero-width
    gap appear between them.
    """
    ordered = sorted(intervals)
    if not ordered:
        return []

    merged = [ordered[0]]
    for current in ordered[1:]:
        last = merged[-1]
        if current.start <= last.end:
            if current.end > last.end:
                merged[-1] = Interval(last.start, current.end)
        else:
            merged.append(current)
    return merged


def subtract(base: Sequence[Interval], blocks: Sequence[Interval]) -> list[Interval]:
    """Everything in `base` that no interval in `blocks` covers."""
    obstacles = normalize(blocks)
    if not obstacles:
        return list(base)

    remaining: list[Interval] = []
    for span in base:
        cursor = span.start
        for block in obstacles:
            if block.end <= cursor:
                continue
            if block.start >= span.end:
                break
            if block.start > cursor:
                remaining.append(Interval(cursor, min(block.start, span.end)))
            cursor = max(cursor, block.end)
            if cursor >= span.end:
                break
        if cursor < span.end:
            remaining.append(Interval(cursor, span.end))
    return remaining


def align_up(moment: datetime, step: timedelta, anchor: datetime) -> datetime:
    """Round `moment` up onto the grid of `step` starting at `anchor`.

    Slots are placed on a wall-clock grid rather than counted from wherever the
    previous appointment happened to end. Without this, one cut finishing at
    10:07 would push every later slot that day to :07, :22, :37 — technically
    correct and useless to a human.
    """
    offset = (moment - anchor) % step
    return moment if not offset else moment + (step - offset)


def slots_in(
    free: Sequence[Interval],
    *,
    duration: timedelta,
    step: timedelta,
    not_before: datetime,
    anchor: datetime,
) -> list[Interval]:
    """Every grid-aligned start where a `duration`-long booking fits entirely."""
    if duration <= timedelta(0) or step <= timedelta(0):
        raise ValueError("duration and step must be positive")

    slots: list[Interval] = []
    for span in free:
        start = align_up(max(span.start, not_before), step, anchor)
        while start + duration <= span.end:
            slots.append(Interval(start, start + duration))
            start += step
    return slots


def local_day_bounds(day: date_type, tz: ZoneInfo) -> Interval:
    """The instants a local calendar day starts and ends.

    Not always 24 hours: on a DST transition day it is 23 or 25.
    """
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return Interval(start, end)


def working_interval(
    day: date_type, start_time: time, end_time: time, tz: ZoneInfo
) -> Interval | None:
    """Turn one wall-clock shift into an instant range.

    Returns `None` when the shift does not survive the conversion — a shift
    lying inside a spring-forward gap collapses to nothing, because that local
    time never happens. Conversion is done through the local timezone rather
    than by adding a fixed offset, so a shift spanning a DST change comes out as
    the real elapsed time (23 or 25 hours in a day, 7 hours for a "6-hour"
    shift) instead of the nominal one.
    """
    start = datetime.combine(day, start_time, tzinfo=tz)
    end = datetime.combine(day, end_time, tzinfo=tz)
    if end <= start:
        return None
    return Interval(start, end)


def compute_free_slots(
    *,
    working: Sequence[Interval],
    busy: Sequence[Interval],
    duration: timedelta,
    step: timedelta,
    not_before: datetime,
    anchor: datetime,
) -> list[Interval]:
    """Working time, minus everything already blocked, cut into bookable slots.

    `busy` mixes appointments and breaks on purpose — to availability they are
    the same thing, so there is no reason for the arithmetic to distinguish them.
    """
    return slots_in(
        subtract(normalize(working), busy),
        duration=duration,
        step=step,
        not_before=not_before,
        anchor=anchor,
    )


def clip(intervals: Iterable[Interval], window: Interval) -> list[Interval]:
    """Trim to `window`, dropping anything fully outside it."""
    trimmed: list[Interval] = []
    for span in intervals:
        start = max(span.start, window.start)
        end = min(span.end, window.end)
        if start < end:
            trimmed.append(Interval(start, end))
    return trimmed
