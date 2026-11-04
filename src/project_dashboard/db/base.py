"""Declarative base class shared by all ORM models."""

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Every constraint and index gets a predictable name. Without this, PostgreSQL
# invents names (e.g. "users_login_key"), and Alembic cannot reliably find a
# constraint later to alter or drop it. The hand-written schema.sql uses the
# same names, which is how our test proves both schemas are identical.
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ix": "ix_%(column_0_label)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CreatedAtMixin:
    # server_default: PostgreSQL fills the value (now()), not Python. It is
    # correct even for rows inserted with plain SQL, e.g. from the Lambda function.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TimestampMixin(CreatedAtMixin):
    # onupdate is applied by the ORM on every UPDATE it issues.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
