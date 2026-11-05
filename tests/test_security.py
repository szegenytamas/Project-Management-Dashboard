"""Unit tests for password hashing and JWT handling (no HTTP, no database)."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from project_dashboard.core.config import Settings
from project_dashboard.core.security import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
    verify_password_async,
)


def test_hash_is_argon2_and_salted() -> None:
    first = hash_password("correct-horse-42")
    second = hash_password("correct-horse-42")

    assert first.startswith("$argon2id$")
    assert first != second  # random salt: same password, different hash
    assert "correct-horse-42" not in first


def test_verify_password() -> None:
    hashed = hash_password("correct-horse-42")

    assert verify_password("correct-horse-42", hashed)
    assert not verify_password("wrong-password", hashed)


async def test_verify_for_unknown_user_is_always_false() -> None:
    assert await verify_password_async("anything", None) is False


def test_token_round_trip(settings: Settings) -> None:
    token = create_access_token(42, settings)

    assert decode_access_token(token, settings) == 42


def test_token_lasts_exactly_one_hour(settings: Settings) -> None:
    token = create_access_token(42, settings)
    # The payload is readable without the key - it is signed, not encrypted.
    payload = jwt.decode(token, options={"verify_signature": False})

    assert payload["sub"] == "42"
    assert payload["exp"] - payload["iat"] == 3600


def test_expired_token_is_rejected(settings: Settings) -> None:
    two_hours_ago = datetime.now(UTC) - timedelta(hours=2)
    token = create_access_token(42, settings, now=two_hours_ago)

    with pytest.raises(InvalidTokenError, match="expired"):
        decode_access_token(token, settings)


def test_token_signed_with_another_key_is_rejected(settings: Settings) -> None:
    attacker = settings.model_copy(update={"jwt_secret_key": SecretStr("x" * 40)})
    forged = create_access_token(42, attacker)

    with pytest.raises(InvalidTokenError, match="Signature"):
        decode_access_token(forged, settings)


def test_tampered_payload_is_rejected(settings: Settings) -> None:
    header, _, signature = create_access_token(42, settings).split(".")
    other_payload = create_access_token(1, settings).split(".")[1]  # claims to be user 1

    with pytest.raises(InvalidTokenError):
        decode_access_token(f"{header}.{other_payload}.{signature}", settings)


def test_unsigned_alg_none_token_is_rejected(settings: Settings) -> None:
    unsigned = jwt.encode(
        {"sub": "42", "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(hours=1)},
        key="",  # the "none" algorithm takes no key; PyJWT treats "" as "no key"
        algorithm="none",
    )

    assert unsigned.endswith(".")  # header.payload. - empty signature
    with pytest.raises(InvalidTokenError):
        decode_access_token(unsigned, settings)


def test_short_jwt_secret_is_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        Settings(jwt_secret_key=SecretStr("too-short"), postgres_password=SecretStr("x"))
