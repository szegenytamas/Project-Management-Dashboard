"""Security hardening: login rate limit, security headers, CORS."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from project_dashboard.core.config import Settings
from project_dashboard.core.rate_limit import RateLimiter
from project_dashboard.main import create_app
from tests.conftest import MakeUser

# ----------------------------------------------------------- the limiter --


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limiter_allows_up_to_the_limit_then_reports_the_wait() -> None:
    clock = FakeClock()
    limiter = RateLimiter(3, 60, clock=clock)

    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    clock.now += 20
    assert limiter.hit("a") == pytest.approx(40)  # the first request expires 60 s after it
    assert limiter.hit("b") is None  # other addresses are counted separately


def test_limiter_lets_the_client_in_again_after_the_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2, 60, clock=clock)
    limiter.hit("a")
    limiter.hit("a")
    assert limiter.hit("a") is not None
    clock.now += 60
    assert limiter.hit("a") is None


def test_rejected_requests_do_not_extend_the_wait() -> None:
    clock = FakeClock()
    limiter = RateLimiter(1, 60, clock=clock)
    limiter.hit("a")
    for _ in range(50):  # hammering while blocked ...
        clock.now += 1
        limiter.hit("a")
    clock.now += 10  # ... still free 60 s after the one ALLOWED request
    assert limiter.hit("a") is None


def test_limiter_memory_is_bounded() -> None:
    clock = FakeClock()
    limiter = RateLimiter(5, 60, max_keys=100, clock=clock)
    for i in range(1000):
        limiter.hit(f"10.0.{i // 256}.{i % 256}")
    assert len(limiter._hits) <= 100


def test_limiter_rejects_nonsense_settings() -> None:
    with pytest.raises(ValueError, match="positive"):
        RateLimiter(0, 60)


# ------------------------------------------------------ through the API --


async def test_login_is_rate_limited_per_client(client: AsyncClient, settings: Settings) -> None:
    attempt = {"login": "nobody", "password": "Wrong-Guess-123!"}
    for _ in range(settings.auth_rate_limit_per_minute):
        assert (await client.post("/login", json=attempt)).status_code == 401

    blocked = await client.post("/login", json=attempt)
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert "Too many attempts" in blocked.json()["detail"]


async def test_registration_shares_the_limit(client: AsyncClient, settings: Settings) -> None:
    for _ in range(settings.auth_rate_limit_per_minute):
        await client.post("/login", json={"login": "nobody", "password": "x"})
    body = {"login": "eve", "password": "Correct-Horse-42!", "repeat_password": "Correct-Horse-42!"}
    assert (await client.post("/auth", json=body)).status_code == 429


async def test_other_endpoints_are_not_limited(
    client: AsyncClient, settings: Settings, make_user: MakeUser
) -> None:
    alice = await make_user("alice")
    for _ in range(settings.auth_rate_limit_per_minute + 5):
        assert (await client.get("/projects", headers=alice.headers)).status_code == 200


# ------------------------------------------------------------- headers --


async def test_security_headers_on_json_responses(client: AsyncClient) -> None:
    for response in (await client.get("/health"), await client.get("/projects")):  # 200, 401
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert (
            response.headers["Content-Security-Policy"]
            == "default-src 'none'; frame-ancestors 'none'"
        )


async def test_swagger_ui_still_loads_its_scripts(client: AsyncClient) -> None:
    response = await client.get("/docs")
    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers  # it needs scripts from a CDN
    assert response.headers["X-Frame-Options"] == "DENY"


# ---------------------------------------------------------------- CORS --


@pytest.fixture
async def cors_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app: FastAPI = create_app(
        settings.model_copy(update={"cors_allowed_origins": ["http://localhost:5173"]})
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_no_cross_origin_access_by_default(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


async def test_configured_origin_is_allowed(cors_client: AsyncClient) -> None:
    preflight = await cors_client.options(
        "/projects",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert preflight.headers["X-Content-Type-Options"] == "nosniff"  # headers wrap CORS too


async def test_other_origins_are_refused(cors_client: AsyncClient) -> None:
    preflight = await cors_client.options(
        "/projects",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers
