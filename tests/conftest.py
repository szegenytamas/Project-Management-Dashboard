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
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass

import asyncpg
import pytest
from alembic import command
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from moto import mock_aws
from pydantic import SecretStr
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from project_dashboard.core.config import Settings, get_settings
from project_dashboard.core.security import create_access_token
from project_dashboard.db.migrate import alembic_config
from project_dashboard.db.models import User
from project_dashboard.db.session import get_session
from project_dashboard.main import create_app
from project_dashboard.storage import S3Storage, get_storage


def asyncpg_dsn(url: URL) -> str:
    """SQLAlchemy URL -> plain DSN that asyncpg understands."""
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


TEST_JWT_SECRET = "test-only-jwt-secret-0123456789abcdef"  # not a real secret


@pytest.fixture(scope="session")
def settings() -> Settings:
    # Fake AWS credentials: moto intercepts every S3 call, nothing leaves this machine.
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        os.environ[name] = "testing"
    # DB password comes from the environment / .env; everything else is fixed for tests.
    base = Settings(
        environment="test",
        jwt_secret_key=SecretStr(TEST_JWT_SECRET),
        s3_bucket="test-documents",
        s3_endpoint_url=None,  # never the local emulator from .env
        aws_region="eu-central-1",
        # Small limits so tests can reach them with tiny files.
        max_file_size_bytes=TEST_MAX_FILE_SIZE,
        project_size_limit_bytes=TEST_PROJECT_LIMIT,
        max_files_per_upload=3,
    )
    # Never run tests against the development (or any real) database.
    return base.model_copy(update={"postgres_db": f"{base.postgres_db}_test"})


TEST_MAX_FILE_SIZE = 20_000
TEST_PROJECT_LIMIT = 50_000


@pytest.fixture(scope="session")
async def s3_session(settings: Settings) -> AsyncIterator[S3Storage]:
    """moto's in-memory S3, started ONCE per test run (starting it is slow)."""
    with mock_aws():
        s3 = S3Storage(settings)
        await s3.ensure_bucket()
        yield s3


@pytest.fixture
def storage(s3_session: S3Storage) -> S3Storage:
    """A real S3 client against moto - with an empty bucket at the start of every test."""
    keys = bucket_keys(s3_session)
    if keys:
        s3_session._client.delete_objects(
            Bucket=s3_session.bucket, Delete={"Objects": [{"Key": k} for k in keys]}
        )
    return s3_session


def bucket_keys(storage: S3Storage) -> list[str]:
    """All object keys currently in the test bucket."""
    response = storage._client.list_objects_v2(Bucket=storage.bucket)
    return sorted(obj["Key"] for obj in response.get("Contents", []))


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


@dataclass
class AuthUser:
    """A user in the test database plus ready-made request headers with a valid JWT."""

    user: User
    headers: dict[str, str]

    @property
    def id(self) -> int:
        return self.user.id


MakeUser = Callable[[str], Awaitable[AuthUser]]


@pytest.fixture
def make_user(db_session: AsyncSession, settings: Settings) -> MakeUser:
    """Create users directly in the DB (skipping argon2) - fast for authz tests."""

    async def factory(login: str) -> AuthUser:
        user = User(login=login, password_hash="not-a-real-hash")
        db_session.add(user)
        await db_session.flush()
        token = create_access_token(user.id, settings)
        return AuthUser(user=user, headers={"Authorization": f"Bearer {token}"})

    return factory


@pytest.fixture
def app(settings: Settings, db_session: AsyncSession, storage: S3Storage) -> FastAPI:
    app = create_app(settings)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    # Dependency overrides: endpoints receive our test settings, the session
    # inside the per-test transaction and the moto-backed S3 client.
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_storage] = lambda: storage
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
