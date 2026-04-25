"""
Alembic migration environment.

Reads DATABASE_URL from settings (asyncpg URL), swaps the driver to psycopg2
for a synchronous migration connection — same pattern as hrms-python.

Run:
    DATABASE_URL=postgresql+asyncpg://masjidkoi:masjidkoi@localhost:5433/masjidkoi \
      uv run alembic upgrade head
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Swap asyncpg → psycopg2 so Alembic runs on a sync connection
_async_url = make_url(settings.DATABASE_URL)
_sync_url = _async_url.set(drivername="postgresql+psycopg2")
config.set_main_option(
    "sqlalchemy.url", _sync_url.render_as_string(hide_password=False)
)

target_metadata = Base.metadata


def include_object(object, name, type_, reflected, compare_to):
    """Only manage objects in the public schema — ignore GoTrue's auth schema."""
    if type_ == "table":
        schema = getattr(object, "schema", None)
        if schema and schema != "public":
            return False
        if reflected and name not in target_metadata.tables:
            return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # Pin search_path=public at connect time so our tables land in public,
        # not in auth (GoTrue's schema, which is first in the role default).
        connect_args={"options": "-c search_path=public"},
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
