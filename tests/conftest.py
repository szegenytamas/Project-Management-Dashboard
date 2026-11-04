"""Shared test fixtures.

pytest discovers this file automatically; every fixture defined here can be
requested by name as a test-function argument.
"""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from project_dashboard.core.config import Settings, get_settings
from project_dashboard.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app()
    # Dependency override: every endpoint that asks for `get_settings`
    # receives our test settings instead of the ones from the environment.
    app.dependency_overrides[get_settings] = lambda: settings
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # ASGITransport calls the app in-process: no server, no network, fast.
    # It does not run startup/shutdown on its own, so we enter the app's
    # lifespan explicitly - otherwise tests would skip e.g. the DB pool setup.
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield client
