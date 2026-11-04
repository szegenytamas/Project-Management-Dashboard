"""Health check endpoint.

Docker, the load balancer and the deploy job call this to ask
"is the process alive and able to answer HTTP?". It must be cheap and
must not require authentication. In Phase 2 we add a database check.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from project_dashboard import __version__
from project_dashboard.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    environment: str


@router.get("/health", response_model=HealthResponse)
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(status="ok", version=__version__, environment=settings.environment)
