"""Handing work off to the background worker.

Wrapped in one place so the request path never imports Celery directly, and so
a broker that is down cannot turn a successful booking into a failed request:
the appointment is already committed by the time we get here, and a customer
does not care that the confirmation email is late.

`send_task` is a blocking network call, so it is the last thing a handler does.
It is switched off under test as well: the suite should pass on a machine with
no broker running, and a test asserting on a booking has no business depending
on Redis being up.
"""

import logging
import uuid
from typing import Literal

from app.core.config import settings

logger = logging.getLogger(__name__)

Event = Literal["created", "cancelled", "rescheduled"]

_TASKS: dict[Event, str] = {
    "created": "app.workers.tasks.notify_appointment_created",
    "cancelled": "app.workers.tasks.notify_appointment_cancelled",
    "rescheduled": "app.workers.tasks.notify_appointment_rescheduled",
}


def appointment_event(event: Event, appointment_id: uuid.UUID) -> None:
    if not settings.NOTIFICATIONS_ENABLED:
        return
    try:
        from app.workers.celery_app import celery_app

        celery_app.send_task(_TASKS[event], args=[str(appointment_id)])
    except Exception as exc:
        logger.warning(
            "could not queue %s notification for appointment %s: %s",
            event,
            appointment_id,
            exc,
        )
