"""Alembic environment: how migrations connect to the database.

Alembic runs this file for every command (upgrade, downgrade, revision
--autogenerate). It works with both entry points:
- `uv run alembic ...` (developer CLI, reads alembic.ini), and
- `python -m project_dashboard.db.migrate` (containers, servers, tests).
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import URL, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from project_dashboard.core.config import get_settings
from project_dashboard.db import models  # noqa: F401  - registers all tables on Base.metadata
from project_dashboard.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate compares the database with this metadata (our ORM models).
target_metadata = Base.metadata


def get_url() -> URL:
    url: URL | None = config.attributes.get("database_url")
    return url if url is not None else get_settings().database_url


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    # PostgreSQL has transactional DDL: if any step of a migration fails,
    # the whole migration is rolled back and the schema stays unchanged.
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # NullPool: a one-off command needs one connection, not a pool.
    engine = create_async_engine(get_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        # Alembic itself is synchronous; run_sync bridges it onto the async connection.
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
