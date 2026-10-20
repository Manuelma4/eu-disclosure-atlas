"""Polite HTTP client: identifies the project, rate-limits per host, retries transient errors."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import httpx

from . import __version__

USER_AGENT = f"eu-disclosure-atlas/{__version__} (+https://github.com/Manuelma4/eu-disclosure-atlas)"
_RETRY_STATUS = {429, 500, 502, 503, 504}


class PoliteClient:
    def __init__(self, min_interval: float = 1.0, timeout: float = 90.0, retries: int = 4) -> None:
        self.min_interval = min_interval
        self.retries = retries
        self._client = httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self._last_request: dict[str, float] = {}

    def get(self, url: str, params: Mapping[str, Any] | None = None) -> httpx.Response:
        return self.request("GET", url, params=params)

    def post(self, url: str, data: Mapping[str, Any] | None = None) -> httpx.Response:
        """Form POST (some registers are stateful search forms); cookies persist across calls."""
        return self.request("POST", url, data=data)

    def request(
        self, method: str, url: str, params: Mapping[str, Any] | None = None, data: Mapping[str, Any] | None = None
    ) -> httpx.Response:
        host = httpx.URL(url).host
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last_request.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            try:
                response = self._client.request(method, url, params=params, data=data)
            except httpx.TransportError:
                if attempt == self.retries:
                    raise
                time.sleep(2**attempt)
                continue
            finally:
                self._last_request[host] = time.monotonic()
            if response.status_code in _RETRY_STATUS and attempt < self.retries:
                retry_after = response.headers.get("Retry-After", "")
                time.sleep(float(retry_after) if retry_after.isdigit() else 2**attempt)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("unreachable")

    def close(self) -> None:
        self._client.close()
