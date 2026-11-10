"""Registration and login: `POST /auth` and `POST /login`."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from project_dashboard.api.deps import limit_auth_requests
from project_dashboard.core.config import Settings, get_settings
from project_dashboard.core.security import create_access_token
from project_dashboard.db.session import SessionDep
from project_dashboard.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserRead
from project_dashboard.services import users

router = APIRouter(tags=["auth"])

_TOO_MANY: dict[int | str, dict[str, Any]] = {
    status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many attempts from this address"}
}


@router.post(
    "/auth",
    status_code=status.HTTP_201_CREATED,
    response_model=UserRead,
    summary="Create a user account",
    responses={status.HTTP_409_CONFLICT: {"description": "Login already taken"}, **_TOO_MANY},
    dependencies=[Depends(limit_auth_requests)],
)
async def register(body: RegisterRequest, session: SessionDep) -> UserRead:
    try:
        user = await users.register_user(session, body.login, body.password)
    except users.LoginTakenError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Login already taken") from None
    return UserRead.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in and receive a JWT (valid for 1 hour)",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Incorrect login or password"},
        **_TOO_MANY,
    },
    dependencies=[Depends(limit_auth_requests)],
)
async def login(
    body: LoginRequest,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    user = await users.authenticate(session, body.login, body.password)
    if user is None:
        # One message for "no such user" and "wrong password": the response
        # must not reveal which logins exist.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect login or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(
        access_token=create_access_token(user.id, settings),
        expires_in=settings.jwt_expire_minutes * 60,
    )
