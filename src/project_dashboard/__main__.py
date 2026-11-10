"""Run the API server: `python -m project_dashboard` or `project-dashboard`."""

import uvicorn

from project_dashboard.core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "project_dashboard.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "local",
        server_header=False,  # do not advertise the server software
    )


if __name__ == "__main__":
    main()
