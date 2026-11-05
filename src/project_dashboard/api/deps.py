"""Reusable FastAPI dependencies.

`CurrentUser` is what protects an endpoint. Declaring it as a parameter is
enough:

    @router.get("/projects")
    async def list_projects(user: CurrentUser): ...

FastAPI then runs `get_current_user` before the endpoint. If the request has
no valid token, the endpoint never runs and the client gets 401.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from project_dashboard.core.config import Settings, get_settings
from project_dashboard.core.security import InvalidTokenError, decode_access_token
from project_dashboard.db.models import User
from project_dashboard.db.session import SessionDep

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
