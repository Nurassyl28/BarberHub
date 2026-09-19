"""shop closures and manual confirmation

Revision ID: 54b6198e0771
Revises: cdef75f707d9
Create Date: 2026-09-19 17:11:19.391779+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "54b6198e0771"
down_revision: str | None = "cdef75f707d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shop_closures",
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "end_date >= start_date", name=op.f("ck_shop_closures_end_not_before_start")
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["barbershops.id"],
            name=op.f("fk_shop_closures_shop_id_barbershops"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop_closures")),
        sa.UniqueConstraint("shop_id", "start_date", "end_date", name="uq_shop_closures_range"),
    )
    op.create_index(op.f("ix_shop_closures_shop_id"), "shop_closures", ["shop_id"], unique=False)
    op.add_column(
        "barbershops",
        sa.Column("requires_confirmation", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("barbershops", "requires_confirmation")
    op.drop_index(op.f("ix_shop_closures_shop_id"), table_name="shop_closures")
    op.drop_table("shop_closures")
