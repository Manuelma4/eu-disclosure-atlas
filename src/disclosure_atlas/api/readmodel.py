"""Read side of the API: DuckDB cursors over the gold views, each pinned to one DuckLake snapshot.

Reads of the current snapshot go to a private in-memory copy of the gold tables, built once per snapshot.
Snapshots are immutable, so the copy is exact. Without it, every request re-evaluates the gold views (latest
version per record, FX join) over DuckLake on the process-wide shared engine. That cost 5 to 12 ms per query
and serialised concurrent requests on that engine (decision 0014: /v1 p95 under 200 ms).
Time travel (``?as_of=``) reads the lake's views directly.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import duckdb

from ..models import Dataset
from .telemetry import query_span

log = logging.getLogger(__name__)
GOLD_TABLES = tuple(dataset.value for dataset in Dataset)


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
    def __init__(self, lake: Any, version_ttl: float = 2.0, cache_gold: bool | None = None) -> None:
        """``lake`` is anything with ``version()`` and ``connect(as_of=...)``: a Lake or a SandboxedLake."""
        self.lake = lake
        self.version_ttl = version_ttl
        self.cache_gold = os.environ.get("ATLAS_GOLD_CACHE", "1") != "0" if cache_gold is None else cache_gold
        self._lock = threading.Lock()
        self._build_lock = threading.Lock()
        self._version: str | None = None
        self._checked = 0.0
        self._cache: tuple[str, duckdb.DuckDBPyConnection] | None = None

    def _current(self) -> str:
        """The latest snapshot id, re-read from the catalog at most every ``version_ttl`` seconds."""
        with self._lock:
            now = time.monotonic()
            if self._version is None or now - self._checked > self.version_ttl:
                self._version = self.lake.version()
                self._checked = now
            return self._version

    def _cached(self, version: str) -> duckdb.DuckDBPyConnection:
        """The in-memory gold copy of ``version``, built by one thread while the others wait for it.

        The previous copy is only dereferenced: cursors still reading it keep it alive until they close.
        """
        cache = self._cache
        if cache is not None and cache[0] == version:
            return cache[1]
        with self._build_lock:
            if self._cache is not None and self._cache[0] == version:
                return self._cache[1]
            start = time.perf_counter()
            source = self.lake.connect(as_of=version)
            con = duckdb.connect()
            con.execute("SET GLOBAL TimeZone = 'UTC'")
            try:
                for table in GOLD_TABLES:
                    con.register("_gold", source.execute(f"SELECT * FROM {table}").fetch_arrow_table())
                    con.execute(f"CREATE TABLE {table} AS SELECT * FROM _gold")
                    con.unregister("_gold")
            finally:
                source.close()
            self._cache = (version, con)
            log.info("Cached gold for snapshot %s in %.0f ms", version, (time.perf_counter() - start) * 1000)
            return con

    @contextmanager
    def snapshot(self, as_of: int | None = None) -> Iterator[Snapshot]:
        """Raises ``lake.UnknownSnapshot`` when ``as_of`` does not exist."""
        version = str(as_of) if as_of is not None else self._current()
        if as_of is None and self.cache_gold:
            cursor = self._cached(version).cursor()
        else:
            cursor = self.lake.connect(as_of=version)
        try:
            yield Snapshot(version=version, cursor=cursor, pinned=as_of is not None)
        finally:
            cursor.close()
