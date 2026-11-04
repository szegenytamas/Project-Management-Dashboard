"""FastAPI application factory.

`create_app()` builds a fresh application. Tests call it to get an isolated
app; uvicorn imports the module-level `app` below.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from project_dashboard import __version__
from project_dashboard.api.routes import health
from project_dashboard.core.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Code before `yield` runs once at startup, code after it at shutdown.
    # Phase 2 opens the database connection pool here and closes it after.
    logger.info("Starting %s v%s", app.title, __version__)
    yield
    logger.info("Shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        redoc_url=None,  # Swagger UI at /docs is enough
    )
    app.include_router(health.router)
    return app


app = create_app()
