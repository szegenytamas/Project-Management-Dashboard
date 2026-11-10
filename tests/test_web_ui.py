"""The browser UI: served by the API, with its own Content-Security-Policy."""

import re
from importlib.resources import files

from httpx import AsyncClient

from project_dashboard.core.headers import STRICT_CSP, WEB_UI_CSP

WEB = files("project_dashboard") / "web"


async def test_root_redirects_to_the_ui(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/app/"


async def test_ui_page_is_served_with_its_own_csp(client: AsyncClient) -> None:
    response = await client.get("/app/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Project Dashboard</title>" in response.text
    assert response.headers["Content-Security-Policy"] == WEB_UI_CSP
    assert response.headers["X-Frame-Options"] == "DENY"


async def test_ui_assets_are_served(client: AsyncClient) -> None:
    script = await client.get("/app/app.js")
    style = await client.get("/app/styles.css")
    assert script.status_code == style.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert style.headers["content-type"].startswith("text/css")


async def test_api_keeps_the_strict_csp(client: AsyncClient) -> None:
    assert (await client.get("/health")).headers["Content-Security-Policy"] == STRICT_CSP


def test_ui_csp_allows_only_this_site() -> None:
    directives = dict(part.strip().split(" ", 1) for part in WEB_UI_CSP.split(";"))
    assert directives["default-src"] == "'none'"
    for name in ("script-src", "style-src", "connect-src"):
        assert directives[name] == "'self'"  # no CDN, no other API, no 'unsafe-inline'
    assert directives["frame-ancestors"] == "'none'"


def test_html_has_no_inline_scripts() -> None:
    # The CSP would block them anyway; this keeps the page working under it.
    html = (WEB / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert scripts and all("src=" in tag for tag in scripts)
    assert not re.search(r"\son[a-z]+\s*=", html)  # no onclick="..." handlers


def test_script_never_writes_html_strings() -> None:
    # Server data is inserted with textContent only; innerHTML would allow XSS.
    script = (WEB / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write", script)


async def test_ui_files_are_revalidated_after_a_deploy(client: AsyncClient) -> None:
    first = await client.get("/app/app.js")
    assert first.headers["Cache-Control"] == "no-cache"
    again = await client.get("/app/app.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304  # unchanged: nothing is downloaded again


def test_html_has_no_inline_styles() -> None:
    # The UI's CSP has no 'unsafe-inline' for styles: a style="..." attribute would be
    # ignored by the browser. Widths are set from app.js through element.style instead.
    html = (WEB / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)  # comments may mention it
    assert not re.search(r"\sstyle\s*=", markup)
    assert "<style" not in html


async def test_logo_is_served_as_svg(client: AsyncClient) -> None:
    response = await client.get("/app/logo.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
