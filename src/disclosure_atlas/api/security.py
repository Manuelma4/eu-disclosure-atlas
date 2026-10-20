"""HTTP hardening for the query API: security headers and per-IP rate limiting (pure ASGI, stream-safe).

The edge (Envoy Gateway, decision 0013) enforces per-client quotas in production; this layer protects the
service on its own, in every environment. Client addresses come from the ASGI scope, so behind a proxy run
uvicorn with --forwarded-allow-ips set to that proxy only (never "*").
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any

Scope = dict[str, Any]
ASGIApp = Any

# JSON responses need no active content; the interactive docs load Swagger UI and ReDoc from a CDN.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
DOCS_CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
    "connect-src 'self'; worker-src blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
DOCS_PATHS = ("/docs", "/redoc")
BASE_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
    (b"cross-origin-opener-policy", b"same-origin"),
    # A public, CORS-enabled read API: other origins may embed its responses.
    (b"cross-origin-resource-policy", b"cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), interest-cohort=()"),
]


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        csp = DOCS_CSP if scope["path"].startswith(DOCS_PATHS) else API_CSP

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                for name, value in [*BASE_HEADERS, (b"content-security-policy", csp.encode())]:
                    if name not in present:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RateLimitMiddleware:
    """Token bucket per client IP: `per_minute` sustained requests with bursts up to `burst`.

    State is per process. It bounds abuse of a single replica; global quotas belong to the gateway.
    """

    exempt = ("/v1/health",)

    def __init__(self, app: ASGIApp, per_minute: int, burst: int, max_clients: int = 100_000) -> None:
        self.app = app
        self.rate = per_minute / 60.0
        self.burst = float(burst)
        self.per_minute = per_minute
        self.max_clients = max_clients
        self.buckets: dict[str, tuple[float, float]] = {}

    def _take(self, key: str, now: float) -> float:
        """Spend one token; return 0 if allowed, else the seconds until a token is available."""
        tokens, updated = self.buckets.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - updated) * self.rate)
        if tokens >= 1:
            self.buckets[key] = (tokens - 1, now)
            return 0.0
        self.buckets[key] = (tokens, now)
        return (1 - tokens) / self.rate

    def _prune(self, now: float) -> None:
        # Full buckets carry no state worth keeping; drop them when the table grows too large.
        full_after = self.burst / self.rate
        self.buckets = {k: v for k, v in self.buckets.items() if now - v[1] < full_after}

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"].startswith(self.exempt):
            await self.app(scope, receive, send)
            return
        now = time.monotonic()
        if len(self.buckets) >= self.max_clients:
            self._prune(now)
        client = scope.get("client")
        wait = self._take(client[0] if client else "unknown", now)
        if wait == 0:
            await self.app(scope, receive, send)
            return
        retry_after = str(max(1, math.ceil(wait)))
        body = json.dumps({
            "type": "about:blank", "title": "Too Many Requests", "status": 429,
            "detail": f"Rate limit of {self.per_minute} requests per minute exceeded", "instance": scope["path"],
        }).encode()
        await send({"type": "http.response.start", "status": 429, "headers": [
            (b"content-type", b"application/problem+json"),
            (b"content-length", str(len(body)).encode()),
            (b"retry-after", retry_after.encode()),
            (b"ratelimit-policy", f"{self.per_minute};w=60;burst={int(self.burst)}".encode()),
        ]})
        await send({"type": "http.response.body", "body": body})


def rate_limit_settings() -> tuple[int, int]:
    """(per_minute, burst) from ATLAS_RATE_LIMIT_PER_MINUTE and ATLAS_RATE_LIMIT_BURST; 0 disables."""
    per_minute = int(os.environ.get("ATLAS_RATE_LIMIT_PER_MINUTE", "600"))
    burst = int(os.environ.get("ATLAS_RATE_LIMIT_BURST", "100"))
    return per_minute, burst
