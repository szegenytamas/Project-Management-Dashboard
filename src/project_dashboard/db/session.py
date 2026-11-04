"""Database engine and per-request sessions.

- The **engine** owns a pool of open connections to PostgreSQL. It is created
  once at startup (in `lifespan`) and closed at shutdown.
- A **session** is a short-lived unit of work: it borrows a connection from the
  pool, tracks the objects you load or add, and writes them on `commit()`.
  Every request gets its own session; sessions are never shared.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from project_dashboard.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,  # test a pooled connection before use (survives DB restarts)
        echo=settings.db_echo,
        connect_args={"timeout": 5},  # seconds; fail fast if the DB is unreachable
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after commit. In async code a
    # refresh would need an `await`, so an expired attribute would raise instead.
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed afterwards.

    The session does NOT commit automatically - the code that changes data
    decides when its unit of work is complete and calls `await session.commit()`.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
