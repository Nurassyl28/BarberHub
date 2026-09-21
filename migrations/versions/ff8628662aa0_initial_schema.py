"""initial schema

Revision ID: ff8628662aa0
Revises:
Create Date: 2026-09-16 07:35:47.898944+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ff8628662aa0"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Extensions first: `citext` backs the case-insensitive email column and
    # `btree_gist` is what lets the `no_double_booking` exclusion constraint mix
    # an equality operator (barber_id) with an overlap operator (the time range).
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "users",
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.Enum("CUSTOMER", "BARBER", "SHOP_OWNER", "ADMIN", name="user_role"),
            server_default="CUSTOMER",
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_table(
        "barbershops",
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("address", sa.String(length=255), nullable=False),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("latitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("timezone", sa.String(length=64), server_default="Asia/Almaty", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_barbershops_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_barbershops")),
    )
    op.create_index(op.f("ix_barbershops_city"), "barbershops", ["city"], unique=False)
    op.create_index(op.f("ix_barbershops_owner_id"), "barbershops", ["owner_id"], unique=False)
    op.create_table(
        "refresh_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(
        "ix_refresh_tokens_user_id_revoked_at",
        "refresh_tokens",
        ["user_id", "revoked_at"],
        unique=False,
    )
    op.create_table(
        "barbers",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("experience_years", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rating", sa.Numeric(precision=3, scale=2), server_default="0", nullable=False),
        sa.Column("reviews_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
            "experience_years >= 0", name=op.f("ck_barbers_experience_years_non_negative")
        ),
        sa.CheckConstraint("rating >= 0 AND rating <= 5", name=op.f("ck_barbers_rating_range")),
        sa.CheckConstraint(
            "reviews_count >= 0", name=op.f("ck_barbers_reviews_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["barbershops.id"],
            name=op.f("fk_barbers_shop_id_barbershops"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_barbers_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_barbers")),
        sa.UniqueConstraint("user_id", "shop_id", name="uq_barbers_user_id_shop_id"),
    )
    op.create_index(op.f("ix_barbers_shop_id"), "barbers", ["shop_id"], unique=False)
    op.create_index(op.f("ix_barbers_user_id"), "barbers", ["user_id"], unique=False)
    op.create_table(
        "services",
        sa.Column("shop_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
            "duration_minutes > 0 AND duration_minutes <= 480",
            name=op.f("ck_services_duration_minutes_range"),
        ),
        sa.CheckConstraint("price >= 0", name=op.f("ck_services_price_non_negative")),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["barbershops.id"],
            name=op.f("fk_services_shop_id_barbershops"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_services")),
    )
    op.create_index(op.f("ix_services_shop_id"), "services", ["shop_id"], unique=False)
    op.create_index(
        "uq_services_shop_id_name_lower",
        "services",
        ["shop_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "appointments",
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("barber_id", sa.UUID(), nullable=False),
        sa.Column("service_id", sa.UUID(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "CONFIRMED",
                "COMPLETED",
                "CANCELLED",
                "NO_SHOW",
                name="appointment_status",
            ),
            server_default="CONFIRMED",
            nullable=False,
        ),
        sa.Column("total_price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("cancelled_by_id", sa.UUID(), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=255), nullable=True),
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
        postgresql.ExcludeConstraint(
            (sa.column("barber_id"), "="),
            (sa.text("tstzrange(start_time, end_time, '[)')"), "&&"),
            where=sa.text("status IN ('PENDING', 'CONFIRMED')"),
            using="gist",
            name="no_double_booking",
        ),
        sa.CheckConstraint("end_time > start_time", name=op.f("ck_appointments_end_after_start")),
        sa.CheckConstraint(
            "total_price >= 0", name=op.f("ck_appointments_total_price_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["barber_id"],
            ["barbers.id"],
            name=op.f("fk_appointments_barber_id_barbers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_id"],
            ["users.id"],
            name=op.f("fk_appointments_cancelled_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["users.id"],
            name=op.f("fk_appointments_customer_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_appointments_service_id_services"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointments")),
    )
    op.create_index(
        "ix_appointments_barber_id_start_time",
        "appointments",
        ["barber_id", "start_time"],
        unique=False,
    )
    op.create_index(
        "ix_appointments_customer_id_start_time",
        "appointments",
        ["customer_id", sa.literal_column("start_time DESC")],
        unique=False,
    )
    op.create_table(
        "barber_breaks",
        sa.Column("barber_id", sa.UUID(), nullable=False),
        sa.Column("start_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_datetime", sa.DateTime(timezone=True), nullable=False),
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
            "end_datetime > start_datetime", name=op.f("ck_barber_breaks_end_after_start")
        ),
        sa.ForeignKeyConstraint(
            ["barber_id"],
            ["barbers.id"],
            name=op.f("fk_barber_breaks_barber_id_barbers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_barber_breaks")),
    )
    op.create_index(
        "ix_barber_breaks_barber_id_start",
        "barber_breaks",
        ["barber_id", "start_datetime"],
        unique=False,
    )
    op.create_table(
        "barber_services",
        sa.Column("barber_id", sa.UUID(), nullable=False),
        sa.Column("service_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["barber_id"],
            ["barbers.id"],
            name=op.f("fk_barber_services_barber_id_barbers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_barber_services_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("barber_id", "service_id", name=op.f("pk_barber_services")),
    )
    op.create_table(
        "working_hours",
        sa.Column("barber_id", sa.UUID(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
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
        sa.CheckConstraint("end_time > start_time", name=op.f("ck_working_hours_end_after_start")),
        sa.CheckConstraint(
            "weekday >= 0 AND weekday <= 6", name=op.f("ck_working_hours_weekday_range")
        ),
        sa.ForeignKeyConstraint(
            ["barber_id"],
            ["barbers.id"],
            name=op.f("fk_working_hours_barber_id_barbers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_working_hours")),
        sa.UniqueConstraint(
            "barber_id", "weekday", "start_time", name="uq_working_hours_barber_day"
        ),
    )
    op.create_index(
        op.f("ix_working_hours_barber_id"), "working_hours", ["barber_id"], unique=False
    )
    op.create_table(
        "reviews",
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("barber_id", sa.UUID(), nullable=False),
        sa.Column("appointment_id", sa.UUID(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name=op.f("ck_reviews_rating_range")),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name=op.f("fk_reviews_appointment_id_appointments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["barber_id"],
            ["barbers.id"],
            name=op.f("fk_reviews_barber_id_barbers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["users.id"],
            name=op.f("fk_reviews_customer_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
        sa.UniqueConstraint("appointment_id", name=op.f("uq_reviews_appointment_id")),
    )
    op.create_index(op.f("ix_reviews_barber_id"), "reviews", ["barber_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_reviews_barber_id"), table_name="reviews")
    op.drop_table("reviews")
    op.drop_index(op.f("ix_working_hours_barber_id"), table_name="working_hours")
    op.drop_table("working_hours")
    op.drop_table("barber_services")
    op.drop_index("ix_barber_breaks_barber_id_start", table_name="barber_breaks")
    op.drop_table("barber_breaks")
    op.drop_index("ix_appointments_customer_id_start_time", table_name="appointments")
    op.drop_index("ix_appointments_barber_id_start_time", table_name="appointments")
    op.drop_table("appointments")
    op.drop_index("uq_services_shop_id_name_lower", table_name="services")
    op.drop_index(op.f("ix_services_shop_id"), table_name="services")
    op.drop_table("services")
    op.drop_index(op.f("ix_barbers_user_id"), table_name="barbers")
    op.drop_index(op.f("ix_barbers_shop_id"), table_name="barbers")
    op.drop_table("barbers")
    op.drop_index("ix_refresh_tokens_user_id_revoked_at", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index(op.f("ix_barbershops_owner_id"), table_name="barbershops")
    op.drop_index(op.f("ix_barbershops_city"), table_name="barbershops")
    op.drop_table("barbershops")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS appointment_status")
    op.execute("DROP TYPE IF EXISTS user_role")
