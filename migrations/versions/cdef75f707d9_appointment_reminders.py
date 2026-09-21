"""appointment reminders

Revision ID: cdef75f707d9
Revises: b2f1c9d40a17
Create Date: 2026-09-18 14:35:38.140063+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cdef75f707d9"
down_revision: str | None = "b2f1c9d40a17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "appointment_reminders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("appointment_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("DAY_BEFORE", "HOURS_BEFORE", name="reminder_kind"),
            nullable=False,
        ),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name=op.f("fk_appointment_reminders_appointment_id_appointments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment_reminders")),
        # The whole idempotency story: a second send simply cannot be recorded.
        sa.UniqueConstraint("appointment_id", "kind", name="uq_appointment_reminders_once"),
    )


def downgrade() -> None:
    op.drop_table("appointment_reminders")
    op.execute("DROP TYPE IF EXISTS reminder_kind")
