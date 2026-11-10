"""Security headers on every HTTP response.

Every response gets:

    X-Content-Type-Options: nosniff     trust our Content-Type; never "guess" that
                                        a downloaded file is HTML or JavaScript
    X-Frame-Options: DENY               may not be shown in a frame (clickjacking)
    Referrer-Policy: no-referrer        never leak our URLs to other sites

plus a Content-Security-Policy (CSP) chosen by path - the browser's list of what
a page may load and run:

    API (JSON, downloads)   default-src 'none'; frame-ancestors 'none'
                            load nothing, run nothing: JSON needs no resources
    /app/  (the web UI)     only its own scripts, styles and API ('self');
                            no inline scripts, nothing from other sites
    /docs  (Swagger UI)     no CSP: it loads scripts from a CDN and runs inline code

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
WEB_UI_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'"
)


class SecurityHeadersMiddleware:
    """`csp_by_prefix`: (path prefix, policy or None), first match wins; else STRICT_CSP."""

    def __init__(
        self, app: ASGIApp, *, csp_by_prefix: tuple[tuple[str, str | None], ...] = ()
    ) -> None:
        self.app = app
        self.csp_by_prefix = csp_by_prefix

    def policy_for(self, path: str) -> str | None:
        for prefix, policy in self.csp_by_prefix:
            if path.startswith(prefix):
                return policy
        return STRICT_CSP

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        policy = self.policy_for(scope["path"])

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in ALWAYS.items():
                    headers[name] = value
                if policy is not None:
                    headers["Content-Security-Policy"] = policy
            await send(message)

        await self.app(scope, receive, send_with_headers)
