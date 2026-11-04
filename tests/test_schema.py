"""The three descriptions of our schema must agree:

ORM models  ==  Alembic migrations  ==  hand-written schema.sql
"""

from importlib.resources import files
from typing import Any

import asyncpg
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import URL, Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from project_dashboard.db.base import Base
from tests.conftest import asyncpg_dsn

RAW_SCHEMA = "raw_sql_check"

COLUMNS = """
    SELECT table_name, column_name, data_type, character_maximum_length,
           is_nullable, column_default, is_identity, identity_generation
    FROM information_schema.columns
    WHERE table_schema = $1 AND table_name <> 'alembic_version'
"""
CONSTRAINTS = """
    SELECT rel.relname, con.conname, con.contype, pg_get_constraintdef(con.oid)
    FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE con.connamespace = $1::regnamespace
      AND con.contype IN ('p', 'f', 'u', 'c')
      AND rel.relname <> 'alembic_version'
"""
INDEXES = """
    SELECT tablename, indexname, indexdef
    FROM pg_indexes
    WHERE schemaname = $1 AND tablename <> 'alembic_version'
"""


async def describe(conn: asyncpg.Connection, schema: str) -> dict[str, set[tuple[Any, ...]]]:
    # Make both schemas look alike: the raw schema's objects are printed with
    # a "raw_sql_check." prefix, which we strip before comparing.
    await conn.execute(f"SET search_path TO {schema}")

    def strip(rows: list[asyncpg.Record]) -> set[tuple[Any, ...]]:
        return {
            tuple(v.replace(f"{schema}.", "") if isinstance(v, str) else v for v in row)
            for row in rows
        }

    return {
        "columns": strip(await conn.fetch(COLUMNS, schema)),
        "constraints": strip(await conn.fetch(CONSTRAINTS, schema)),
        "indexes": strip(await conn.fetch(INDEXES, schema)),
    }


async def test_models_match_migrations(engine: AsyncEngine) -> None:
    """Fails if someone changes a model but forgets to create a migration."""

    def diff(sync_conn: Connection) -> list[Any]:
        changes: list[Any] = compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
        return changes

    async with engine.connect() as conn:
        assert await conn.run_sync(diff) == []


async def test_schema_sql_matches_migrations(migrated_database: URL) -> None:
    """Fails if schema.sql and the migrations describe different databases."""
    script = (files("project_dashboard.db") / "schema.sql").read_text(encoding="utf-8")
    conn = await asyncpg.connect(asyncpg_dsn(migrated_database))
    try:
        await conn.execute(f"DROP SCHEMA IF EXISTS {RAW_SCHEMA} CASCADE")
        await conn.execute(f"CREATE SCHEMA {RAW_SCHEMA}")
        await conn.execute(f"SET search_path TO {RAW_SCHEMA}")
        await conn.execute(script)

        migrated = await describe(conn, "public")
        handwritten = await describe(conn, RAW_SCHEMA)
    finally:
        await conn.execute(f"DROP SCHEMA IF EXISTS {RAW_SCHEMA} CASCADE")
        await conn.close()

    for part in ("columns", "constraints", "indexes"):
        assert handwritten[part] == migrated[part], part
    assert len(migrated["columns"]) == 23  # sanity check: we compared real data
