"""Security headers on every HTTP response.

The API answers JSON (and file downloads), so the browser should never treat a
response as something to render, run or embed. These headers tell it so:

    X-Content-Type-Options: nosniff     trust our Content-Type; never "guess" that
                                        a downloaded file is HTML or JavaScript
    X-Frame-Options: DENY               the API may not be shown in a frame
                                        (clickjacking) - for older browsers
    Content-Security-Policy:            load nothing, run nothing, no frames:
      default-src 'none';               a JSON response needs no resources at all
      frame-ancestors 'none'
    Referrer-Policy: no-referrer        never leak our URLs to other sites

Swagger UI (`/docs`) is an HTML page that loads scripts from a CDN, so it gets
everything except the strict CSP.

Not set, on purpose:
- Strict-Transport-Security: only valid over HTTPS; the deployment serves plain
  HTTP for now (see docs/security.md).

Written as a pure ASGI middleware rather than `@app.middleware("http")`: it only
edits the response headers and passes the body through untouched, so streamed
file downloads stay streamed.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ALWAYS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
STRICT_CSP = "default-src 'none'; frame-ancestors 'none'"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, html_paths: frozenset[str] = frozenset()) -> None:
        self.app = app
        self.html_paths = html_paths  # pages that must load scripts (Swagger UI)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        strict = scope["path"] not in self.html_paths

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in ALWAYS.items():
                    headers[name] = value
                if strict:
                    headers["Content-Security-Policy"] = STRICT_CSP
            await send(message)

        await self.app(scope, receive, send_with_headers)
