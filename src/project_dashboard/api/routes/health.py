"""Health check endpoints.

- `/health` (liveness): "is the process alive and able to answer HTTP?"
  Cheap, no database. Docker's HEALTHCHECK uses it; if it fails, the
  container is restarted.
- `/health/ready` (readiness): "can this instance serve real requests?"
  Also checks the database. If it fails, a load balancer should stop sending
  traffic here - but restarting the app would not fix a database outage.

Neither requires authentication: monitoring tools have no user token.
"""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from project_dashboard import __version__
from project_dashboard.core.config import Settings, get_settings
from project_dashboard.db.session import SessionDep

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    database: Literal["ok", "unreachable"]


@router.get("/health", response_model=HealthResponse)
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(status="ok", version=__version__, environment=settings.environment)


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness(session: SessionDep, response: Response) -> ReadinessResponse:
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        logger.exception("Readiness check failed: database unreachable")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="unavailable", database="unreachable")
    return ReadinessResponse(status="ok", database="ok")
