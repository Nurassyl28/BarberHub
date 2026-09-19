"""Email rendering — no SMTP, no database."""

import pytest

from app.workers.email import render

CONTEXT = {
    "customer": "Dana",
    "barber": "Aidar",
    "shop": "Sharp Cuts",
    "address": "Abay 10",
    "service": "Haircut",
    "price": "5000 KZT",
    "when": "Monday 21 September at 09:00",
    "cutoff": 120,
    "reason": "",
    "horizon": "tomorrow",
}


@pytest.mark.parametrize(
    "template",
    [
        "appointment_created",
        "appointment_cancelled",
        "appointment_rescheduled",
        "appointment_reminder",
        "barber_appointment_created",
    ],
)
def test_every_template_renders_without_leftover_placeholders(template: str) -> None:
    subject, body = render(template, **CONTEXT)

    assert subject and body
    assert "$" not in subject, subject
    assert "$" not in body, body


def test_the_confirmation_names_the_essentials() -> None:
    subject, body = render("appointment_created", **CONTEXT)

    assert "Sharp Cuts" in subject
    assert "Dana" in body
    assert "Aidar" in body
    assert "Monday 21 September at 09:00" in body
    assert "5000 KZT" in body
    assert "120" in body


def test_a_cancellation_reason_is_included_when_given() -> None:
    _, body = render("appointment_cancelled", **CONTEXT | {"reason": "Reason: Ill"})

    assert "Reason: Ill" in body


def test_a_missing_key_does_not_raise() -> None:
    """`safe_substitute`: a missing value must not break a whole send."""
    subject, body = render("appointment_created", customer="Dana")

    assert "Dana" in body
    assert isinstance(subject, str)


def test_the_reminder_says_how_far_away_it_is() -> None:
    subject, _ = render("appointment_reminder", **CONTEXT)

    assert "tomorrow" in subject
