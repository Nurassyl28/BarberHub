"""Rendering and sending mail.

Templates are plain `string.Template` substitution rather than Jinja: these are
five short transactional messages, and adding a template engine to the worker
image would buy nothing.
"""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from string import Template

from app.core.config import settings


@dataclass(frozen=True, slots=True)
class Email:
    to: str
    subject: str
    body: str


_TEMPLATES: dict[str, tuple[str, str]] = {
    "appointment_created": (
        "Your booking at $shop is confirmed",
        "Hi $customer,\n\n"
        "You are booked in with $barber at $shop.\n\n"
        "  When:    $when\n"
        "  Service: $service\n"
        "  Price:   $price\n\n"
        "Need to change it? You can cancel or reschedule up to "
        "$cutoff minutes before.\n",
    ),
    "appointment_cancelled": (
        "Your booking at $shop was cancelled",
        "Hi $customer,\n\n"
        "Your $service with $barber on $when has been cancelled.\n"
        "$reason\n"
        "You can book again any time.\n",
    ),
    "appointment_rescheduled": (
        "Your booking at $shop has moved",
        "Hi $customer,\n\nYour $service with $barber is now at $when.\n",
    ),
    "appointment_reminder": (
        "Reminder: $service at $shop $horizon",
        "Hi $customer,\n\n"
        "This is a reminder for your $service with $barber.\n\n"
        "  When:  $when\n"
        "  Where: $address\n",
    ),
    "barber_appointment_created": (
        "New booking: $customer on $when",
        "$barber,\n\n$customer booked $service with you at $when.\n",
    ),
}


def render(template: str, **context: object) -> tuple[str, str]:
    subject, body = _TEMPLATES[template]
    values = {key: str(value) for key, value in context.items()}
    return (
        Template(subject).safe_substitute(values),
        Template(body).safe_substitute(values),
    )


def send(email: Email) -> None:
    """Deliver one message over SMTP.

    Synchronous on purpose: Celery workers are not async, and a blocking SMTP
    call inside a worker process costs nothing that matters.
    """
    message = EmailMessage()
    message["From"] = f"{settings.EMAILS_FROM_NAME} <{settings.EMAILS_FROM_EMAIL}>"
    message["To"] = email.to
    message["Subject"] = email.subject
    message.set_content(email.body)

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
        if settings.SMTP_TLS:
            smtp.starttls()
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(message)
