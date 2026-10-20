"""Read side of the API: DuckDB over the gold views, reopened when the lake version changes."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import duckdb

from ..lake import Lake
from .telemetry import query_span


@dataclass
class Snapshot:
    """One request's view of the data: a version (used for ETags) and a private cursor."""

    version: str
    cursor: duckdb.DuckDBPyConnection

    def rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        with query_span(sql):
            result = self.cursor.execute(sql, params or [])
            columns = [d[0] for d in result.description]
            return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]

    def one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        rows = self.rows(sql, params)
        return rows[0] if rows else None


class ReadModel:
    def __init__(self, lake: Lake, version_ttl: float = 2.0) -> None:
        self.lake = lake
        self.version_ttl = version_ttl
        self._lock = threading.Lock()
        self._version: str | None = None
        self._checked = 0.0
        self._con: duckdb.DuckDBPyConnection | None = None

    def _current(self) -> tuple[str, duckdb.DuckDBPyConnection]:
        with self._lock:
            now = time.monotonic()
            if self._con is None or now - self._checked > self.version_ttl:
                version = self.lake.version()
                if version != self._version or self._con is None:
                    self._con = self.lake.connect()
                    self._version = version
                self._checked = now
            assert self._version is not None
            return self._version, self._con.cursor()

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        version, cursor = self._current()
        try:
            yield Snapshot(version=version, cursor=cursor)
        finally:
            cursor.close()
