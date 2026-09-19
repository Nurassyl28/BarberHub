"""SQLAlchemy models.

Importing this package registers every table on `Base.metadata`, which is what
Alembic autogenerate and `create_all` rely on.
"""

from app.models.appointment import Appointment
from app.models.barber import Barber, barber_services
from app.models.barbershop import Barbershop
from app.models.enums import AppointmentStatus, UserRole
from app.models.notification import AppointmentReminder, ReminderKind
from app.models.review import Review
from app.models.schedule import BarberBreak, WorkingHours
from app.models.service import Service
from app.models.user import RefreshToken, User

__all__ = [
    "Appointment",
    "AppointmentReminder",
    "AppointmentStatus",
    "Barber",
    "BarberBreak",
    "Barbershop",
    "RefreshToken",
    "ReminderKind",
    "Review",
    "Service",
    "User",
    "UserRole",
    "WorkingHours",
    "barber_services",
]
