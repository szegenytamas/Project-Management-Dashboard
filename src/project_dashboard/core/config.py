"""Application configuration.

All configuration comes from environment variables (or a local `.env` file).
Pydantic validates and converts them once at startup, so a typo such as
`PORT=80a` fails immediately with a clear error instead of crashing later.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    """Return the settings, created once per process.

    Used as a FastAPI dependency, so tests can replace it with
    `app.dependency_overrides[get_settings] = lambda: Settings(...)`.
    """
    return Settings()
