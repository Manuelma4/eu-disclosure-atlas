"""Public REST API (FastAPI): /v1 read endpoints over gold, SSE live stream, OpenAPI 3.1."""

from .app import create_app

__all__ = ["create_app"]
