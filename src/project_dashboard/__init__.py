"""Project Management Dashboard API."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the version in pyproject.toml.
    __version__ = version("project-dashboard")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"
