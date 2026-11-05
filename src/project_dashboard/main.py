"""FastAPI application factory.

`create_app()` builds a fresh application. Tests call it with their own
settings; uvicorn calls it in factory mode:

    uvicorn project_dashboard.main:create_app --factory

There is deliberately no module-level `app = create_app()`: that would read
the settings (and require a DB password) the moment anything imports this module.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from project_dashboard import __version__
from project_dashboard.api.routes import auth, health, projects
from project_dashboard.core.config import Settings, get_settings
from project_dashboard.core.errors import register_error_handlers
from project_dashboard.db.session import create_engine, create_sessionmaker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: create the connection pool. No connection is opened yet - the
    # first one is made when a request needs the database.
    settings: Settings = app.state.settings
    engine = create_engine(settings)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    logger.info("Starting %s v%s", app.title, __version__)
    yield
    # Shutdown: close all pooled connections cleanly.
    await engine.dispose()
    logger.info("Shut down")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        redoc_url=None,  # Swagger UI at /docs is enough
    )
    app.state.settings = settings
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(projects.router)
    return app
