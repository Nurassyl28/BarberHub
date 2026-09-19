"""Celery application and beat schedule."""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "barberhub",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Acknowledge only after the task finishes, so a worker killed mid-send
    # leaves the job on the queue instead of dropping it.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=300,
    task_soft_time_limit=240,
    # Tests run tasks inline; production does not.
    task_always_eager=settings.ENVIRONMENT == "test",
    task_eager_propagates=True,
)

celery_app.conf.beat_schedule = {
    "send-appointment-reminders": {
        "task": "app.workers.tasks.send_appointment_reminders",
        # Every 15 minutes rather than hourly: a reminder promised "2 hours
        # before" should not land 59 minutes late.
        "schedule": crontab(minute="*/15"),
    },
    "expire-stale-pending": {
        "task": "app.workers.tasks.expire_stale_pending",
        "schedule": crontab(minute=0),
    },
    "recompute-barber-ratings": {
        "task": "app.workers.tasks.recompute_barber_ratings",
        "schedule": crontab(hour=3, minute=30),
    },
}
