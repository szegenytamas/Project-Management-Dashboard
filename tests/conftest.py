"""Shared test fixtures.

pytest discovers this file automatically; every fixture defined here can be
requested by name as a test-function argument.

Database strategy
-----------------
1. Once per test run: (re)create a separate database "<POSTGRES_DB>_test" and
   migrate it with Alembic - so every run also tests the migrations.
2. For every test: open a transaction, give the test a session inside it,
   and ROLL BACK at the end. Each test sees an empty database, and nothing a
   test writes survives into the next one - even when the code under test
   calls `commit()` (it only releases a SAVEPOINT inside our transaction).
3. At the end of the run: downgrade to "base" - so the downgrade is tested too.
"""

import asyncio
import re
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest
from alembic import command
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from project_dashboard.core.config import Settings, get_settings
from project_dashboard.db.migrate import alembic_config
from project_dashboard.db.session import get_session
from project_dashboard.main import create_app


def asyncpg_dsn(url: URL) -> str:
    """SQLAlchemy URL -> plain DSN that asyncpg understands."""
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


TEST_JWT_SECRET = "test-only-jwt-secret-0123456789abcdef"  # not a real secret


@pytest.fixture(scope="session")
def settings() -> Settings:
    # DB password comes from the environment / .env; the JWT key is fixed for tests.
    base = Settings(environment="test", jwt_secret_key=SecretStr(TEST_JWT_SECRET))
    # Never run tests against the development (or any real) database.
    return base.model_copy(update={"postgres_db": f"{base.postgres_db}_test"})


async def _recreate_database(settings: Settings) -> None:
    name = settings.postgres_db
    # The name goes into SQL as an identifier, which cannot be a query parameter,
    # so we only allow a safe character set.
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError(f"Unsafe test database name: {name!r}")
    # Connect to the built-in maintenance database "postgres" to create ours.
    maintenance = settings.database_url.set(database="postgres")
    try:
        conn = await asyncpg.connect(asyncpg_dsn(maintenance), timeout=5)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.exit(
            f"Cannot connect to PostgreSQL at {settings.postgres_host}:{settings.postgres_port} "
            f"({exc}). Start it with: docker compose up -d db",
            returncode=1,
        )
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_database(settings: Settings) -> Iterator[URL]:
    # A plain (sync) fixture: Alembic's env.py starts its own event loop.
    asyncio.run(_recreate_database(settings))
    config = alembic_config(settings.database_url)
    command.upgrade(config, "head")
    yield settings.database_url
    command.downgrade(config, "base")


@pytest.fixture(scope="session")
async def engine(migrated_database: URL) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            # session.commit() / rollback() only affect a SAVEPOINT; the outer
            # transaction stays open until we roll it back below.
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
def app(settings: Settings, db_session: AsyncSession) -> FastAPI:
    app = create_app(settings)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    # Dependency overrides: endpoints receive our test settings and the
    # session inside the per-test transaction instead of the real ones.
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_get_session
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # ASGITransport calls the app in-process: no server, no network, fast.
    # It does not run startup/shutdown on its own, so we enter the app's
    # lifespan explicitly - otherwise the engine/sessionmaker would not exist.
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield client
