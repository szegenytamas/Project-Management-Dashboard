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
from fastapi.middleware.cors import CORSMiddleware

from project_dashboard import __version__
from project_dashboard.api.routes import auth, documents, health, projects
from project_dashboard.core.config import Settings, get_settings
from project_dashboard.core.errors import register_error_handlers
from project_dashboard.core.headers import SecurityHeadersMiddleware
from project_dashboard.core.rate_limit import RateLimiter
from project_dashboard.db.session import create_engine, create_sessionmaker
from project_dashboard.storage import S3Storage

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: create the connection pool. No connection is opened yet - the
    # first one is made when a request needs the database.
    settings: Settings = app.state.settings
    engine = create_engine(settings)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    # One S3 client per process (boto3 clients are thread-safe and reusable).
    storage = S3Storage(settings)
    if settings.s3_create_bucket:  # local emulator only; in AWS the bucket is provisioned
        await storage.ensure_bucket()
    app.state.storage = storage
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
    # One limiter per application: tests get a fresh one with every app.
    app.state.auth_rate_limiter = RateLimiter(settings.auth_rate_limit_per_minute, 60)
    register_error_handlers(app)
    add_middleware(app, settings)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(documents.router)
    return app


def add_middleware(app: FastAPI, settings: Settings) -> None:
    # The middleware added LAST runs FIRST (outermost). Security headers go on
    # last, so they are also on the answers CORS gives without calling the app.
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,  # an explicit list, never "*"
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
            expose_headers=["Content-Disposition"],  # lets a web UI read download names
            allow_credentials=False,  # the token travels in a header, not in cookies
            max_age=600,
        )
    docs = {p for p in (app.docs_url, app.swagger_ui_oauth2_redirect_url) if p is not None}
    app.add_middleware(SecurityHeadersMiddleware, html_paths=frozenset(docs))
