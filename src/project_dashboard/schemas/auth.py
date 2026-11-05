"""Request and response models for registration and login.

Pydantic validates every incoming field before our code runs. Invalid input
never reaches the database; FastAPI answers 422 with a description of what
was wrong.
"""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def _normalize_login(value: str) -> str:
    # "Alice" and "alice" are the same account - otherwise users could create
    # look-alike accounts and forget which spelling they used.
    return value.lower()


Login = Annotated[
    str,
    Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="3-64 characters: letters, digits, '_', '.', '-'. Case-insensitive.",
        examples=["alice"],
    ),
    AfterValidator(_normalize_login),
]
# Length limits: 8 is the usual minimum; the maximum stops someone from
# sending megabytes of "password" to make the slow hash function burn CPU.
Password = Annotated[str, Field(min_length=8, max_length=128, examples=["correct-horse-42"])]


class RegisterRequest(BaseModel):
    login: Login
    password: Password
    repeat_password: str = Field(examples=["correct-horse-42"])

    @model_validator(mode="after")
    def passwords_match(self) -> Self:
        if self.password != self.repeat_password:
            raise ValueError("Passwords do not match")
        return self


class LoginRequest(BaseModel):
    # Deliberately NOT the strict Login/Password rules: a login attempt that
    # cannot match any account simply fails with 401, like a wrong password.
    login: Annotated[str, Field(max_length=64), AfterValidator(_normalize_login)]
    password: Annotated[str, Field(max_length=128)]


class UserRead(BaseModel):
    """What the API reveals about a user - never the password hash."""

    model_config = ConfigDict(from_attributes=True)  # can be built from an ORM object

    id: int
    login: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - a scheme name, not a password
    expires_in: int = Field(description="Lifetime in seconds")
