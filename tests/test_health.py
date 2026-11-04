from httpx import ASGITransport, AsyncClient

from project_dashboard import __version__
from project_dashboard.core.config import Settings
from project_dashboard.main import create_app


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "environment": "test",
    }


async def test_readiness_ok_when_database_reachable(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_readiness_503_when_database_unreachable(settings: Settings) -> None:
    # A real app (no overrides) pointed at a port where nothing listens.
    broken = settings.model_copy(update={"postgres_port": 1})
    app = create_app(broken)
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "unreachable"}


async def test_unknown_route_returns_json_404(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


async def test_openapi_schema_is_available(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert {"/health", "/health/ready"} <= set(response.json()["paths"])
