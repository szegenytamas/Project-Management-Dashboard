# syntax=docker/dockerfile:1

# ============================================================== builder ====
# Stage 1 has the build tools (uv) and creates a virtual environment.
FROM python:3.13-slim AS builder

# Copy the uv binary from its official image, pinned to the version we use locally.
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# 1) Install ONLY the third-party dependencies, from the lockfile.
#    This layer is cached and reused as long as pyproject.toml/uv.lock
#    don't change - so editing our code doesn't reinstall FastAPI every build.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# 2) Now add our own code and install it as a regular (non-editable) package.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ============================================================== runtime ====
# Stage 2 is what ships: Python + the virtual environment, nothing else.
# No uv, no source tree, no dev tools -> smaller image, smaller attack surface.
FROM python:3.13-slim AS runtime

# Never run as root inside the container.
RUN groupadd --system app && useradd --system --gid app --no-create-home app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
# Amazon RDS certificate authorities, to verify the database's TLS certificate
# (POSTGRES_SSL_MODE=verify-full). Public certificates, not secrets.
COPY certs/rds-global-bundle.pem /app/certs/rds-global-bundle.pem

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production \
    HOST=0.0.0.0 \
    PORT=8000

USER app
WORKDIR /app
EXPOSE 8000

# Docker marks the container "unhealthy" if /health stops answering.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

# --proxy-headers: honour X-Forwarded-* only from trusted addresses (uvicorn's default:
#   127.0.0.1). No proxy runs in front of the API today, so the client address - which the
#   login rate limit counts by - comes from the connection and cannot be faked by a header.
# --no-server-header: do not advertise "uvicorn" in every response.
CMD ["uvicorn", "project_dashboard.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
