"""Run database migrations: `python -m project_dashboard.db.migrate`.

This is how migrations run in Docker Compose and on the server. It needs no
alembic.ini: the migration scripts ship inside the installed package, and the
database URL comes from the same Settings the app uses.
"""

import logging
from importlib.resources import files
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import URL

from project_dashboard.core.config import get_settings


def alembic_config(
    database_url: URL | None = None, connect_args: dict[str, Any] | None = None
) -> Config:
    config = Config()
    config.set_main_option("script_location", str(files("project_dashboard.db") / "migrations"))
    # Passed as Python objects, not through the config file: a "%" in a password
    # would otherwise be misread by Alembic's ini-file parser, and a TLS context
    # cannot be written into a text file at all.
    config.attributes["database_url"] = database_url
    config.attributes["connect_args"] = connect_args
    return config


def main() -> None:
    # Without alembic.ini nobody configures logging, and Alembic would run silently.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    settings = get_settings()
    command.upgrade(alembic_config(settings.database_url, settings.database_connect_args()), "head")


if __name__ == "__main__":
    main()
