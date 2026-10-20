"""Public REST API (FastAPI): /v1 read endpoints over gold, SSE live stream, OpenAPI 3.1."""

from typing import Any

__all__ = ["create_app"]


def __getattr__(name: str) -> Any:
    # Lazy, so `api.schemas` (also used by the event publisher) imports without FastAPI installed.
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(name)
