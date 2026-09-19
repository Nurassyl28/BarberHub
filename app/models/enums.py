"""Enumerations stored as native PostgreSQL enum types."""

from enum import StrEnum


class UserRole(StrEnum):
    CUSTOMER = "CUSTOMER"
    BARBER = "BARBER"
    SHOP_OWNER = "SHOP_OWNER"
    ADMIN = "ADMIN"


class AppointmentStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL

    @property
    def blocks_slot(self) -> bool:
        """Whether an appointment in this status occupies the barber's time.

        This is the same set the `no_double_booking` exclusion constraint filters
        on — keep the two in sync.
        """
        return self in _ACTIVE


_TERMINAL = frozenset(
    {
        AppointmentStatus.COMPLETED,
        AppointmentStatus.CANCELLED,
        AppointmentStatus.NO_SHOW,
    }
)

_ACTIVE = frozenset({AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED})

#: Statuses that occupy a barber's calendar, in the literal form the DDL uses.
ACTIVE_STATUS_SQL = "'PENDING', 'CONFIRMED'"
