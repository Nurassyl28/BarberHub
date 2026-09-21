"""Alembic environment.

Runs against the *sync* driver (psycopg): migrations are a one-shot script, and
the async engine buys nothing here while costing an event loop.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.base import Base
from app.models import *  # noqa: F403  — import side effect: registers every table

config = context.config
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

#: Indexes created with raw SQL because SQLAlchemy cannot express them
#: (trigram GIN, expression and partial indexes). They are real and intended,
#: but invisible to the model metadata, so autogenerate would propose dropping
#: them on every run.
RAW_SQL_INDEXES = {
    "ix_barbershops_name_trgm",
    "ix_barbershops_description_trgm",
    "ix_services_name_trgm",
    "ix_barbershops_city_lower",
    "ix_barbershops_coordinates",
    "ix_barbershops_is_active",
}


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    if type_ == "index" and name in RAW_SQL_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
