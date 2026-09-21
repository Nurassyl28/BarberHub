"""Time helpers.

One place produces "now" so tests can freeze it and so no module reaches for a
naive `datetime.now()` by accident.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
