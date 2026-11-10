"""Reusable FastAPI dependencies.

`CurrentUser` is what protects an endpoint. Declaring it as a parameter is
enough:

    @router.get("/projects")
    async def list_projects(user: CurrentUser): ...

FastAPI then runs `get_current_user` before the endpoint. If the request has
no valid token, the endpoint never runs and the client gets 401.
"""

import math
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from project_dashboard.core.config import Settings, get_settings
from project_dashboard.core.rate_limit import RateLimiter
from project_dashboard.core.security import InvalidTokenError, decode_access_token
from project_dashboard.db.models import User
from project_dashboard.db.session import SessionDep
from project_dashboard.services.permissions import (
    Action,
    DocumentAccess,
    ProjectAccess,
    get_document_access,
    get_project_access,
)

# IDs are BIGINT in PostgreSQL. Without the upper bound, /project/99999999999999999999
# would pass validation and crash the database driver (500 instead of 422).
MAX_DB_ID = 2**63 - 1
ProjectId = Annotated[int, Path(gt=0, le=MAX_DB_ID, description="Project id")]
DocumentId = Annotated[int, Path(gt=0, le=MAX_DB_ID, description="Document id")]

# Reads "Authorization: Bearer <token>" and adds the "Authorize" button to
# Swagger UI. auto_error=False: we raise our own, consistent 401 below.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT from POST /login")


def _unauthorized(detail: str) -> HTTPException:
    # The WWW-Authenticate header tells the client which scheme to use (RFC 6750).
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Not authenticated")
    try:
        user_id = decode_access_token(credentials.credentials, settings)
    except InvalidTokenError:
        raise _unauthorized("Invalid or expired token") from None
    # The token proves who you WERE when it was issued; the account must still exist.
    user = await session.get(User, user_id)
    if user is None:
        raise _unauthorized("Invalid or expired token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def limit_auth_requests(request: Request) -> None:
    """Brute-force protection for POST /auth and POST /login (per client IP).

    429 Too Many Requests with a Retry-After header (seconds), as RFC 6585 says.
    Invalid requests count too: they also cost us work.
    """
    limiter: RateLimiter = request.app.state.auth_rate_limiter
    client = request.client.host if request.client else "unknown"
    retry_after = limiter.hit(client)
    if retry_after is not None:
        seconds = max(1, math.ceil(retry_after))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many attempts, try again in {seconds} seconds",
            headers={"Retry-After": str(seconds)},
        )


def require_project_access(action: Action) -> Callable[..., Awaitable[ProjectAccess]]:
    """Build a dependency that grants access to `/project/{project_id}/...` for one action.

    Usage in a route:
        access: Annotated[ProjectAccess, Depends(require_project_access(Action.DELETE))]

    The chain is: valid token (CurrentUser) -> membership -> role allows action.
    The endpoint only runs if all three pass.
    """

    async def dependency(
        project_id: ProjectId, user: CurrentUser, session: SessionDep
    ) -> ProjectAccess:
        return await get_project_access(session, project_id, user.id, action)

    return dependency


def require_document_access(action: Action) -> Callable[..., Awaitable[DocumentAccess]]:
    """The same chain for `/document/{document_id}`: the document's project decides."""

    async def dependency(
        document_id: DocumentId, user: CurrentUser, session: SessionDep
    ) -> DocumentAccess:
        return await get_document_access(session, document_id, user.id, action)

    return dependency
