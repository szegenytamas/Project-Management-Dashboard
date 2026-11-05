"""Application-wide error handling."""

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from project_dashboard.services.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """422 response WITHOUT the submitted values.

    FastAPI's default 422 body repeats each invalid value under "input".
    For `POST /auth` with a too-short password, that would send the password
    back in the response - and into any log or proxy that records responses.
    We keep the useful parts (where, what, why) and drop the value itself.
    """
    if not isinstance(exc, RequestValidationError):  # registered only for this type
        raise TypeError(type(exc))
    errors = [
        {key: value for key, value in error.items() if key not in {"input", "url"}}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": jsonable_encoder(errors)},
    )


_DOMAIN_STATUS: dict[type[DomainError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    ConflictError: status.HTTP_409_CONFLICT,
}


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Translate a service-layer error into its HTTP status, in one place."""
    if not isinstance(exc, DomainError):  # registered only for this type
        raise TypeError(type(exc))
    code = next(
        (code for family, code in _DOMAIN_STATUS.items() if isinstance(exc, family)),
        status.HTTP_400_BAD_REQUEST,
    )
    return JSONResponse(status_code=code, content={"detail": exc.detail})


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(DomainError, domain_error_handler)
