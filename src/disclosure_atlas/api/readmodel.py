"""Read side of the API: DuckDB cursors over the gold views, each pinned to one DuckLake snapshot."""

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
    """One request's view of the data: a version (the snapshot id, used for ETags) and a private cursor."""

    version: str
    cursor: duckdb.DuckDBPyConnection
    pinned: bool = False
    """True when the client asked for this snapshot (``?as_of=``): the response can never change."""

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

    def _current(self) -> str:
        """The latest snapshot id, re-read from the catalog at most every ``version_ttl`` seconds."""
        with self._lock:
            now = time.monotonic()
            if self._version is None or now - self._checked > self.version_ttl:
                self._version = self.lake.version()
                self._checked = now
            return self._version

    @contextmanager
    def snapshot(self, as_of: int | None = None) -> Iterator[Snapshot]:
        """Raises ``lake.UnknownSnapshot`` when ``as_of`` does not exist."""
        version = str(as_of) if as_of is not None else self._current()
        cursor = self.lake.connect(as_of=version)
        try:
            yield Snapshot(version=version, cursor=cursor, pinned=as_of is not None)
        finally:
            cursor.close()
