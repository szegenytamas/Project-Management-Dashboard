"""Password hashing and JSON Web Tokens.

Passwords
---------
We never store a password, only a **hash**: a one-way fingerprint. At login we
hash the attempt the same way and compare. argon2id is deliberately slow and
memory-hungry, and every hash has a random **salt**, so a stolen database
cannot be cracked with precomputed tables and brute force is expensive.

Hashing takes tens of milliseconds of pure CPU work. Done directly inside an
`async def` it would freeze the event loop - every other request would wait -
so the async helpers below run it in a worker thread.

JWT
---
A JWT is three base64 parts: header.payload.signature. The payload is only
*encoded*, not encrypted - anyone can read it, so it holds no secrets. The
signature (HMAC-SHA256 with our secret key) makes it tamper-proof: change one
character and verification fails. Our payload:
    sub  - the user id (as a string, per the JWT standard)
    iat  - issued at (Unix time)
    exp  - expires at: iat + 60 minutes
"""

from datetime import UTC, datetime, timedelta
from functools import cache

import jwt
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool

from project_dashboard.core.config import Settings

_password_hash = PasswordHash.recommended()  # argon2id with recommended parameters


class InvalidTokenError(Exception):
    """The token is missing, malformed, expired or has a bad signature."""


# --------------------------------------------------------------- passwords --
def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


@cache
def _dummy_hash() -> str:
    return hash_password("not-a-real-password-only-used-for-timing")


async def hash_password_async(password: str) -> str:
    return await run_in_threadpool(hash_password, password)


async def verify_password_async(password: str, password_hash: str | None) -> bool:
    """Check a password; with `password_hash=None` (unknown user) always False.

    For an unknown user we still verify against a dummy hash, so the response
    takes as long as for a wrong password. Otherwise an attacker could tell
    from the response time which logins exist ("user enumeration").
    """
    if password_hash is None:
        await run_in_threadpool(verify_password, password, _dummy_hash())
        return False
    return await run_in_threadpool(verify_password, password, password_hash)


# --------------------------------------------------------------------- JWT --
def create_access_token(user_id: int, settings: Settings, *, now: datetime | None = None) -> str:
    issued_at = now or datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def decode_access_token(token: str, settings: Settings) -> int:
    """Return the user id from a valid token, or raise InvalidTokenError."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            # Accept ONLY our algorithm. Never let the token's own header choose
            # it - the classic "alg: none" attack relies on exactly that.
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "iat", "exp"]},
        )
        return int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError) as exc:  # expired, bad signature, bad sub ...
        raise InvalidTokenError(str(exc)) from exc
