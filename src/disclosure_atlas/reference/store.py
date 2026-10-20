"""Shared helpers for the entity reference caches: which identifiers the lake needs, and merge-on-write."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from ..lake import Lake

LEI_SQL = """
    SELECT DISTINCT issuer_lei FROM insider_transactions WHERE issuer_lei IS NOT NULL
    UNION SELECT DISTINCT issuer_lei FROM net_short_positions WHERE issuer_lei IS NOT NULL
"""
ISIN_SQL = """
    SELECT DISTINCT isin FROM insider_transactions WHERE isin IS NOT NULL
    UNION SELECT DISTINCT isin FROM net_short_positions WHERE isin IS NOT NULL
"""


def lake_identifiers(lake: Lake, sql: str) -> set[str]:
    """Identifiers (LEIs or ISINs) used by the gold views, upper-cased."""
    con = lake.connect()
    try:
        return {str(value).strip().upper() for (value,) in con.execute(sql).fetchall() if value and str(value).strip()}
    finally:
        con.close()


def read_cache(path: Path, schema: pa.Schema) -> pa.Table:
    return pq.read_table(path, schema=schema) if path.exists() else schema.empty_table()


def cached_keys(path: Path, schema: pa.Schema, key: str) -> set[str]:
    return set(read_cache(path, schema).column(key).to_pylist())


def merge_write(path: Path, schema: pa.Schema, key: str, records: Iterable[dict[str, Any]]) -> int:
    """Replace the cached rows of every key in ``records`` and keep the others; returns the total row count.

    Writes to a temporary file first, so a reader never sees a half-written cache.
    """
    new = pa.Table.from_pylist(list(records), schema=schema)
    old = read_cache(path, schema)
    if len(new):
        old = old.filter(pc.invert(pc.is_in(old.column(key), value_set=new.column(key).unique())))
    table = pa.concat_tables([old, new]).sort_by([(key, "ascending")])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)
    return len(table)
