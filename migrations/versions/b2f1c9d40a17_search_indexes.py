"""search indexes

Revision ID: b2f1c9d40a17
Revises: ff8628662aa0
Create Date: 2026-09-18 00:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2f1c9d40a17"
down_revision: str | None = "ff8628662aa0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `q` and `service` search with ILIKE '%term%'. A btree index cannot help a
    # leading wildcard at all; trigram GIN indexes are built for exactly this.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_barbershops_name_trgm "
        "ON barbershops USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_barbershops_description_trgm "
        "ON barbershops USING gin (description gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_services_name_trgm ON services USING gin (name gin_trgm_ops)"
    )

    # `?city=Almaty` compares lower(city), which the plain city index cannot serve.
    op.execute("CREATE INDEX ix_barbershops_city_lower ON barbershops (lower(city))")

    # Backs the bounding-box prefilter that runs before the haversine maths.
    op.execute(
        "CREATE INDEX ix_barbershops_coordinates ON barbershops (latitude, longitude) "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    )

    # Every list query filters on this first.
    op.execute(
        "CREATE INDEX ix_barbershops_is_active ON barbershops (is_active) "
        "WHERE is_active"
    )


def downgrade() -> None:
    for index in (
        "ix_barbershops_is_active",
        "ix_barbershops_coordinates",
        "ix_barbershops_city_lower",
        "ix_services_name_trgm",
        "ix_barbershops_description_trgm",
        "ix_barbershops_name_trgm",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index}")
