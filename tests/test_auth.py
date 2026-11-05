"""API tests for POST /auth, POST /login and the CurrentUser dependency."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter, FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_dashboard.api.deps import CurrentUser
from project_dashboard.core.config import Settings
from project_dashboard.core.security import create_access_token
from project_dashboard.db.models import User

PASSWORD = "Correct-Horse-42!"


async def register(client: AsyncClient, login: str = "alice", password: str = PASSWORD) -> Response:
    return await client.post(
        "/auth", json={"login": login, "password": password, "repeat_password": password}
    )


async def login(client: AsyncClient, login: str = "alice", password: str = PASSWORD) -> Response:
    return await client.post("/login", json={"login": login, "password": password})


# ---------------------------------------------------------------- register --
async def test_register_creates_user(client: AsyncClient, db_session: AsyncSession) -> None:
    response = await register(client)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "login", "created_at"}  # never the password or its hash
    assert body["login"] == "alice"

    user = await db_session.scalar(select(User).where(User.login == "alice"))
    assert user is not None
    assert user.password_hash.startswith("$argon2id$")


async def test_register_duplicate_login_returns_409(client: AsyncClient) -> None:
    await register(client)

    response = await register(client)

    assert response.status_code == 409
    assert response.json() == {"detail": "Login already taken"}


async def test_login_is_case_insensitive(client: AsyncClient) -> None:
    assert (await register(client, "Alice")).json()["login"] == "alice"

    assert (await register(client, "ALICE")).status_code == 409
    assert (await login(client, "aLiCe")).status_code == 200


async def test_register_passwords_must_match(client: AsyncClient) -> None:
    response = await client.post(
        "/auth", json={"login": "alice", "password": PASSWORD, "repeat_password": "different-1"}
    )

    assert response.status_code == 422
    assert "Passwords do not match" in response.text


@pytest.mark.parametrize(
    ("login_value", "password"),
    [
        ("al", PASSWORD),  # login too short
        ("a" * 65, PASSWORD),  # login too long
        ("alice smith", PASSWORD),  # space not allowed
        ("alice", "Sh0rt!pass"),  # password: 10 characters
        ("alice", "Aa1!" + "x" * 125),  # password: 129 characters
    ],
)
async def test_register_rejects_invalid_input(
    client: AsyncClient, login_value: str, password: str
) -> None:
    response = await register(client, login_value, password)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("password", "missing"),
    [
        ("correct-horse-42!", "an uppercase letter"),
        ("CORRECT-HORSE-42!", "a lowercase letter"),
        ("Correct-Horse-XX!", "a digit"),
        ("CorrectHorse4242", "a special character"),
        ("correcthorsebattery", "an uppercase letter, a digit, a special character"),
    ],
)
async def test_password_must_contain_all_character_classes(
    client: AsyncClient, password: str, missing: str
) -> None:
    response = await register(client, "alice", password)

    assert response.status_code == 422
    assert f"Password must contain {missing}" in response.text


@pytest.mark.parametrize("password", ["Alice-Smith-1!x", "XX-alice-2024!", "ALICE#password1"])
async def test_password_must_not_contain_the_login(client: AsyncClient, password: str) -> None:
    response = await register(client, "alice", password)

    assert response.status_code == 422
    assert "Password must not contain the login" in response.text


async def test_strong_password_is_accepted(client: AsyncClient) -> None:
    response = await register(client, "alice", "Tr0ub4dor&3-horse")

    assert response.status_code == 201


async def test_validation_errors_do_not_echo_the_password(client: AsyncClient) -> None:
    response = await register(client, "alice", "Secret7!")  # 8 chars: too short

    assert response.status_code == 422
    assert "Secret7!" not in response.text
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "password"]
    assert error["type"] == "string_too_short"


# ------------------------------------------------------------------- login --
async def test_login_returns_bearer_token(client: AsyncClient) -> None:
    await register(client)

    response = await login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    assert body["access_token"].count(".") == 2  # header.payload.signature


async def test_wrong_password_and_unknown_user_look_identical(client: AsyncClient) -> None:
    await register(client)

    wrong_password = await login(client, "alice", "wrong-password")
    unknown_user = await login(client, "nobody", PASSWORD)

    for response in (wrong_password, unknown_user):
        assert response.status_code == 401
        assert response.json() == {"detail": "Incorrect login or password"}
        assert response.headers["www-authenticate"] == "Bearer"


# -------------------------------------------------- CurrentUser dependency --
# There are no protected endpoints yet (they come in Phase 4), so the tests
# mount a tiny one that only exists in the test app.
@pytest.fixture
async def protected_client(app: FastAPI, client: AsyncClient) -> AsyncIterator[AsyncClient]:
    router = APIRouter()

    @router.get("/test/me")
    async def me(user: CurrentUser) -> dict[str, str]:
        return {"login": user.login}

    app.include_router(router)
    yield client


async def test_protected_endpoint_with_valid_token(protected_client: AsyncClient) -> None:
    await register(protected_client)
    token = (await login(protected_client)).json()["access_token"]

    response = await protected_client.get("/test/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"login": "alice"}


@pytest.mark.parametrize(
    "headers",
    [
        {},  # no header at all
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Basic YWxpY2U6c2VjcmV0"},  # wrong scheme
    ],
)
async def test_protected_endpoint_rejects_missing_or_bad_token(
    protected_client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await protected_client.get("/test/me", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_protected_endpoint_rejects_expired_token(
    protected_client: AsyncClient, settings: Settings
) -> None:
    user_id = (await register(protected_client)).json()["id"]
    expired = create_access_token(user_id, settings, now=datetime.now(UTC) - timedelta(hours=2))

    response = await protected_client.get(
        "/test/me", headers={"Authorization": f"Bearer {expired}"}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired token"}


async def test_token_of_deleted_user_is_rejected(
    protected_client: AsyncClient, settings: Settings
) -> None:
    token = create_access_token(999_999, settings)  # valid signature, no such user

    response = await protected_client.get("/test/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
