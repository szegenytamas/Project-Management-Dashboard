"""Business logic for user accounts.

Services know nothing about HTTP: they take plain values, use the database
session and raise domain exceptions. The routes translate those into HTTP
status codes. That keeps the logic easy to test and to reuse.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from project_dashboard.core.security import hash_password_async, verify_password_async
from project_dashboard.db.models import User


class LoginTakenError(Exception):
    pass


async def register_user(session: AsyncSession, login: str, password: str) -> User:
    user = User(login=login, password_hash=await hash_password_async(password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # We do NOT check "does this login exist?" before inserting: two
        # requests could both pass that check at the same moment (a race
        # condition). The UNIQUE constraint is the only reliable judge.
        if "uq_users_login" in str(exc.orig):
            raise LoginTakenError(login) from exc
        raise
    return user


async def set_password(session: AsyncSession, user: User, password: str) -> None:
    """Replace a user's password (stored as a new argon2 hash with a new salt)."""
    user.password_hash = await hash_password_async(password)
    await session.commit()


async def authenticate(session: AsyncSession, login: str, password: str) -> User | None:
    """Return the user if login and password match, otherwise None."""
    user = await session.scalar(select(User).where(User.login == login))
    password_ok = await verify_password_async(password, user.password_hash if user else None)
    return user if password_ok else None
