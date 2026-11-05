"""Application configuration.

All configuration comes from environment variables (or a local `.env` file).
Pydantic validates and converts them once at startup, so a typo such as
`POSTGRES_PORT=54a2` fails immediately with a clear error instead of crashing later.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # unrelated variables in the environment are fine
    )

    app_name: str = "Project Management Dashboard"
    environment: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    host: str = "127.0.0.1"
    port: int = 8000

    # --- Database ---------------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "dashboard"
    # No default on purpose: a password must always come from the environment.
    # SecretStr hides the value in logs, reprs and error messages ("**********").
    postgres_password: SecretStr
    postgres_db: str = "dashboard"
    db_pool_size: int = 5
    db_echo: bool = False  # True prints every SQL statement - handy for learning

    # --- Authentication -------------------------------------------------
    # The key that signs every JWT. Whoever knows it can forge tokens for any
    # user, so: no default, at least 32 characters, never committed.
    # Generate one with:  python -c "import secrets; print(secrets.token_hex(32))"
    jwt_secret_key: SecretStr = Field(min_length=32)
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_expire_minutes: int = Field(default=60, gt=0)  # the brief: tokens last 1 hour

    # --- Document storage (S3) ------------------------------------------
    s3_bucket: str = "project-dashboard-documents"
    s3_endpoint_url: str | None = None  # None = real AWS; set for the local emulator
    aws_region: str = "eu-central-1"
    s3_create_bucket: bool = False  # local emulator only: create the bucket at startup
    # Leave EMPTY in AWS: boto3 then uses the EC2 instance's IAM role, so no
    # real AWS key is ever written into a file. Only the local emulator needs
    # these (any dummy value works there).
    aws_access_key_id: str | None = None
    aws_secret_access_key: SecretStr | None = None

    max_file_size_bytes: int = Field(default=10 * 1024 * 1024, gt=0)  # 10 MB per file
    project_size_limit_bytes: int = Field(default=50 * 1024 * 1024, gt=0)  # 50 MB per project
    max_files_per_upload: int = Field(default=10, gt=0)

    @property
    def database_url(self) -> URL:
        # URL.create escapes special characters (e.g. "@" or "/" in a password)
        # that would break a hand-written "postgresql://user:pass@host/db" string.
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """Return the settings, created once per process.

    Used as a FastAPI dependency, so tests can replace it with
    `app.dependency_overrides[get_settings] = lambda: Settings(...)`.
    """
    return Settings()  # required values (the DB password) come from the environment
