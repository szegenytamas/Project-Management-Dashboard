"""Every migration can be applied, undone and applied again (a "stairway" test).

The session fixture already upgrades the test database to head at the start of
the run and downgrades it at the end. This test goes further, on a database of
its own: for EACH revision, oldest first, it runs upgrade -> downgrade -> upgrade
and checks that a full downgrade leaves nothing behind. A downgrade that forgets
to drop an index, or an upgrade that cannot run twice, fails here - not on the
day a release has to be rolled back.
"""

import asyncio
from collections.abc import Iterator

import asyncpg
import pytest
from alembic import command
from alembic.script import ScriptDirectory

from project_dashboard.core.config import Settings
from project_dashboard.db.migrate import alembic_config
from tests.conftest import _recreate_database, asyncpg_dsn

LEFTOVERS = """
    SELECT 'table ' || tablename FROM pg_tables
        WHERE schemaname = 'public' AND tablename <> 'alembic_version'
    UNION ALL
    SELECT 'sequence ' || sequencename FROM pg_sequences WHERE schemaname = 'public'
    UNION ALL
    SELECT 'type ' || typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd')
"""


@pytest.fixture
def scratch_settings(settings: Settings) -> Iterator[Settings]:
    scratch = settings.model_copy(update={"postgres_db": f"{settings.postgres_db}_migrations"})
    asyncio.run(_recreate_database(scratch))
    yield scratch
    asyncio.run(_drop_database(scratch))


async def _drop_database(settings: Settings) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(settings.database_url.set(database="postgres")))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{settings.postgres_db}" WITH (FORCE)')
    finally:
        await conn.close()


async def _leftovers(settings: Settings) -> list[str]:
    conn = await asyncpg.connect(asyncpg_dsn(settings.database_url))
    try:
        return [row[0] for row in await conn.fetch(LEFTOVERS)]
    finally:
        await conn.close()


def test_every_migration_upgrades_downgrades_and_upgrades_again(
    scratch_settings: Settings,
) -> None:
    config = alembic_config(scratch_settings.database_url)
    revisions = list(reversed(list(ScriptDirectory.from_config(config).walk_revisions())))
    assert revisions, "no migrations found"

    for revision in revisions:
        previous = revision.down_revision or "base"
        assert isinstance(previous, str), "merge revisions are not expected here"
        command.upgrade(config, revision.revision)
        command.downgrade(config, previous)
        command.upgrade(config, revision.revision)

    command.downgrade(config, "base")
    assert asyncio.run(_leftovers(scratch_settings)) == []

    command.upgrade(config, "head")  # and the empty database can be rebuilt from scratch
