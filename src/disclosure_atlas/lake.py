"""Medallion lake on DuckLake 1.0 (see docs/decisions/0001 and 0005).

bronze/<source>/YYYY/MM/<ts>-<sha>.<ext>   raw responses, immutable files, never published
bronze/<source>/_manifest.jsonl            provenance of every raw document
silver.<dataset>  (DuckLake table)          typed records, append-only, one commit (snapshot) per document
reference/<name>.parquet                   reference data (e.g. ECB euro reference rates)
gold                                        DuckDB views pinned to one DuckLake snapshot: latest version of each
                                            record, minus superseded ones, plus derived columns (first_seen_at,
                                            is_open_market, amount_eur...)
                                            plus issuers, instruments and insider_transactions_enriched
                                            (GLEIF, FIRDS and OpenFIGI reference data; see reference/enrich.py)

The DuckLake catalog and data path come from the environment:

ATLAS_CATALOG     DuckLake metadata location. ``postgres:host=... dbname=...`` in production (and whenever
                  several processes share the lake), ``sqlite:<file>``, or a DuckDB file. Default:
                  ``<root>/catalog.ducklake``, a DuckDB file that only one process may open at a time.
ATLAS_CATALOG_SCHEMA  PostgreSQL schema holding the DuckLake catalog tables (default ``ducklake``).
ATLAS_DATA_PATH   Where Parquet data files go: a local folder (default ``<root>/data/``) or ``s3://bucket/prefix/``
                  (S3-compatible storage such as Cloudflare R2; credentials from ATLAS_S3_* below, otherwise the
                  AWS credential chain).
ATLAS_S3_ENDPOINT, ATLAS_S3_KEY_ID, ATLAS_S3_SECRET, ATLAS_S3_REGION   optional S3 settings.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .connectors.base import RawDocument, Record
from .models import RECORD_TYPES, Dataset, arrow_schema
from .privacy import load_key
from .reference.enrich import REFERENCE_TABLES, empty_select, enriched_sql

CATALOG = "atlas"
"""Name under which the DuckLake catalog is attached in every engine."""

COMMIT_AUTHOR = "disclosure-atlas"

_LATEST = """
    SELECT * EXCLUDE (_rn) FROM (
        SELECT *,
               row_number() OVER (PARTITION BY record_id ORDER BY fetched_at DESC) AS _rn,
               min(fetched_at) OVER (PARTITION BY record_id) AS first_seen_at
        FROM {silver}
    )
    WHERE _rn = 1 AND NOT is_superseded
"""


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _gold_sql(dataset: Dataset, schema: str, has_fx: bool) -> str:
    latest = _LATEST.format(silver=f"{schema}.silver_{dataset.value}")
    if dataset is not Dataset.INSIDER_TRANSACTIONS:
        return latest
    amount_eur = (
        "CASE WHEN t.currency = 'EUR' THEN t.amount ELSE t.amount / fx.rate END"
        if has_fx
        else "CAST(NULL AS DOUBLE)"
    )
    fx_join = (
        f"ASOF LEFT JOIN {schema}.fx_rates fx ON t.currency = fx.currency AND t.transaction_date >= fx.date"
        if has_fx
        else ""
    )
    return f"""
        SELECT t.*,
               (t.nature IN ('buy', 'sell') AND t.instrument_type = 'share' AND coalesce(t.on_venue, false))
                   AS is_open_market,
               date_diff('day', t.transaction_date, CAST(timezone('UTC', t.published_at) AS DATE))
                   AS filing_lag_days,
               {amount_eur} AS amount_eur
        FROM ({latest}) AS t
        {fx_join}
    """


class UnknownSnapshot(LookupError):
    """Raised when a requested snapshot id does not exist (yet) in the lake."""


class _Engine:
    """One DuckDB database per process and catalog, with the DuckLake catalog attached once.

    A DuckDB-file catalog can be attached by a single database at a time, so every ``Lake`` pointing at the same
    catalog shares this engine and works through cursors. Gold views live in one schema per snapshot
    (``snap_<id>``) of the engine's in-memory catalog and are created on first use.
    """

    _engines: dict[tuple[str, str], _Engine] = {}
    _engines_lock = threading.Lock()

    def __init__(self, catalog: str, data_path: str) -> None:
        self.catalog = catalog
        self.data_path = data_path
        self.lock = threading.Lock()
        self.con = duckdb.connect()
        self.con.execute("SET GLOBAL TimeZone = 'UTC'")
        self.con.execute("INSTALL ducklake; LOAD ducklake")
        if catalog.startswith("postgres:"):
            self.con.execute("INSTALL postgres; LOAD postgres")
        elif catalog.startswith("sqlite:"):
            self.con.execute("INSTALL sqlite; LOAD sqlite")
        if data_path.startswith(("s3://", "r2://")):
            self._s3_secret()
        else:
            Path(data_path).mkdir(parents=True, exist_ok=True)
        options = f"DATA_PATH {_quote(data_path)}"
        if catalog.startswith("postgres:"):
            options += f", METADATA_SCHEMA {_quote(os.environ.get('ATLAS_CATALOG_SCHEMA', 'ducklake'))}"
        self.con.execute(f"ATTACH {_quote('ducklake:' + catalog)} AS {CATALOG} ({options})")
        self._ensure_tables()
        self.first_snapshot = self._first_snapshot()
        self._views: set[str] = set()

    @classmethod
    def get(cls, catalog: str, data_path: str) -> _Engine:
        with cls._engines_lock:
            key = (catalog, data_path)
            if key not in cls._engines:
                cls._engines[key] = cls(catalog, data_path)
            return cls._engines[key]

    @classmethod
    def close(cls, catalog: str, data_path: str) -> None:
        with cls._engines_lock:
            engine = cls._engines.pop((catalog, data_path), None)
        if engine is not None:
            engine.con.close()

    def _s3_secret(self) -> None:
        self.con.execute("INSTALL httpfs; LOAD httpfs")
        options = {"TYPE": "s3"}
        if key_id := os.environ.get("ATLAS_S3_KEY_ID"):
            options |= {"KEY_ID": key_id, "SECRET": os.environ.get("ATLAS_S3_SECRET", "")}
        else:
            options["PROVIDER"] = "credential_chain"
        if endpoint := os.environ.get("ATLAS_S3_ENDPOINT"):
            options |= {"ENDPOINT": endpoint.removeprefix("https://"), "URL_STYLE": "path"}
        options["REGION"] = os.environ.get("ATLAS_S3_REGION", "auto")
        body = ", ".join(f"{k} {v if k in ('TYPE', 'PROVIDER') else _quote(v)}" for k, v in options.items())
        self.con.execute(f"CREATE OR REPLACE SECRET atlas_s3 ({body})")

    def _ensure_tables(self) -> None:
        """Create silver tables on first use; add columns that the models gained since (additive evolution)."""
        cur = self.con.cursor()
        try:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.silver")
            for dataset in Dataset:
                schema = arrow_schema(RECORD_TYPES[dataset])
                table = f"{CATALOG}.silver.{dataset.value}"
                existing = {
                    name
                    for (name,) in cur.execute(
                        "SELECT column_name FROM duckdb_columns() WHERE database_name = ? AND schema_name = 'silver' "
                        "AND table_name = ?",
                        [CATALOG, dataset.value],
                    ).fetchall()
                }
                cur.register("_empty", schema.empty_table())
                if not existing:
                    cur.execute(f"CREATE TABLE {table} AS SELECT * FROM _empty")
                else:
                    types = dict(cur.execute("SELECT column_name, column_type FROM (DESCRIBE _empty)").fetchall())
                    for name in schema.names:
                        if name not in existing:
                            cur.execute(f'ALTER TABLE {table} ADD COLUMN "{name}" {types[name]}')
                cur.unregister("_empty")
        finally:
            cur.close()

    def _first_snapshot(self) -> int:
        """The snapshot that created the silver tables; earlier snapshots cannot be queried."""
        cur = self.con.cursor()
        try:
            row = cur.execute(
                f"SELECT min(snapshot_id) FROM {CATALOG}.snapshots() "
                "WHERE list_contains(changes['tables_created'], ?)",
                [f"silver.{Dataset.NET_SHORT_POSITIONS.value}"],
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            cur.close()

    def views(self, snapshot: int, fx_path: Path) -> str:
        """Schema holding silver_*, fx_rates, reference tables and the gold views as of ``snapshot``.

        Created once per process and per set of reference files present (a missing one reads as an empty table).
        """
        has_fx = fx_path.exists()
        references = {name: fx_path.parent / file for name, (file, _) in REFERENCE_TABLES.items()}
        present = sum(1 << i for i, path in enumerate(references.values()) if path.exists())
        schema = f"snap_{snapshot}" + ("" if has_fx else "_nofx") + (f"_r{present}" if present else "")
        with self.lock:
            if schema in self._views:
                return schema
            cur = self.con.cursor()
            try:
                cur.execute("BEGIN")
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS memory.{schema}")
                for dataset in Dataset:
                    cur.execute(
                        f"CREATE OR REPLACE VIEW memory.{schema}.silver_{dataset.value} AS "
                        f"SELECT * FROM {CATALOG}.silver.{dataset.value} AT (VERSION => {snapshot})"
                    )
                if has_fx:
                    cur.execute(
                        f"CREATE OR REPLACE VIEW memory.{schema}.fx_rates AS "
                        f"SELECT * FROM read_parquet({_quote(str(fx_path))})"
                    )
                for dataset in Dataset:
                    cur.execute(
                        f"CREATE OR REPLACE VIEW memory.{schema}.{dataset.value} AS "
                        f"{_gold_sql(dataset, f'memory.{schema}', has_fx)}"
                    )
                for name, path in references.items():
                    source = (
                        f"SELECT * FROM read_parquet({_quote(str(path))})"
                        if path.exists()
                        else empty_select(REFERENCE_TABLES[name][1])
                    )
                    cur.execute(f"CREATE OR REPLACE VIEW memory.{schema}.{name} AS {source}")
                for name, sql in enriched_sql(f"memory.{schema}").items():
                    cur.execute(f"CREATE OR REPLACE VIEW memory.{schema}.{name} AS {sql}")
                cur.execute("COMMIT")
            finally:
                cur.close()
            self._views.add(schema)
            return schema


class Lake:
    def __init__(self, root: Path | str = "lake", *, catalog: str | None = None, data_path: str | None = None) -> None:
        # Absolute, so the DATA_PATH stored in a DuckDB-file catalog is the same whichever working directory or
        # relative spelling opened it (DuckLake refuses to attach a catalog with a different DATA_PATH).
        self.root = Path(root).expanduser().resolve()
        self.bronze = self.root / "bronze"
        self.silver = self.root / "silver"  # Parquet silver of the pre-DuckLake layout, read by migrate()
        self.catalog = catalog or os.environ.get("ATLAS_CATALOG") or str(self.root / "catalog.ducklake")
        default_data = str(self.root / "data") + os.sep
        self.data_path = data_path or os.environ.get("ATLAS_DATA_PATH") or default_data

    def pseudonym_key(self) -> bytes:
        return load_key(self.root)

    @property
    def _engine(self) -> _Engine:
        if not self.catalog.startswith(("postgres:", "sqlite:", "md:")):
            self.root.mkdir(parents=True, exist_ok=True)
        return _Engine.get(self.catalog, self.data_path)

    def close(self) -> None:
        """Detach the catalog in this process (lets another process open a DuckDB-file catalog)."""
        _Engine.close(self.catalog, self.data_path)

    # Bronze ---------------------------------------------------------------------------------------------------

    def write_bronze(self, doc: RawDocument) -> Path:
        folder = self.bronze / doc.source / doc.fetched_at.strftime("%Y/%m")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{doc.fetched_at:%Y%m%dT%H%M%S%fZ}-{doc.sha256[:12]}{doc.suffix}"
        path.write_bytes(doc.content)
        entry = {
            "path": str(path.relative_to(self.root)),
            "source": doc.source,
            "url": doc.url,
            "params": doc.params,
            "content_type": doc.content_type,
            "fetched_at": doc.fetched_at.isoformat(),
            "sha256": doc.sha256,
            "bytes": len(doc.content),
            **({"meta": doc.meta} if doc.meta else {}),
        }
        with (self.bronze / doc.source / "_manifest.jsonl").open("a", encoding="utf-8") as manifest:
            manifest.write(json.dumps(entry, default=str) + "\n")
        return path

    def bronze_manifest(self, source: str) -> Iterator[dict[str, Any]]:
        """Every manifest entry of one source, oldest first."""
        path = self.bronze / source / "_manifest.jsonl"
        if path.exists():
            with path.open(encoding="utf-8") as manifest:
                for line in manifest:
                    if line.strip():
                        yield json.loads(line)

    def read_bronze(self, entry: dict[str, Any]) -> RawDocument:
        """Rebuild the raw document described by a manifest entry, so silver can be re-parsed without refetching."""
        path = self.root / entry["path"]
        return RawDocument(
            source=entry["source"],
            url=entry["url"],
            params=entry.get("params") or {},
            content=path.read_bytes(),
            content_type=entry.get("content_type"),
            fetched_at=datetime.fromisoformat(entry["fetched_at"]),
            suffix=path.suffix,
            meta=entry.get("meta") or {},
        )

    # Silver ---------------------------------------------------------------------------------------------------

    def write_silver(
        self, dataset: Dataset, source: str, records: Sequence[Record], *, provenance: dict[str, Any] | None = None
    ) -> int | None:
        """Insert one document's records in a single DuckLake transaction; returns the committed snapshot id."""
        if not records:
            return None
        table = pa.Table.from_pylist([r.model_dump() for r in records], schema=arrow_schema(RECORD_TYPES[dataset]))
        return self._commit(dataset, source, table, {"records": len(records), **(provenance or {})})

    def _commit(self, dataset: Dataset, source: str, table: pa.Table, extra: dict[str, Any]) -> int:
        info = {"dataset": dataset.value, "source": source, **extra}
        cur = self._engine.con.cursor()
        try:
            cur.register("batch", table)
            cur.execute("BEGIN")
            cur.execute(f"INSERT INTO {CATALOG}.silver.{dataset.value} BY NAME SELECT * FROM batch")
            cur.execute(
                f"CALL {CATALOG}.set_commit_message(?, ?, extra_info => ?)",
                [COMMIT_AUTHOR, f"{source}: {len(table)} {dataset.value}", json.dumps(info, default=str)],
            )
            cur.execute("COMMIT")
            return int(cur.execute(f"SELECT id FROM {CATALOG}.last_committed_snapshot()").fetchone()[0])
        finally:
            cur.close()

    # Snapshots ------------------------------------------------------------------------------------------------

    def version(self) -> str:
        """The current DuckLake snapshot id: changes with every commit, and pins ETags and read models."""
        return str(self.current_snapshot())

    def current_snapshot(self) -> int:
        cur = self._engine.con.cursor()
        try:
            return int(cur.execute(f"SELECT id FROM {CATALOG}.current_snapshot()").fetchone()[0])
        finally:
            cur.close()

    def snapshots(self, since: int | None = None) -> list[dict[str, Any]]:
        """Committed snapshots (id, time, changes, author, message, extra info as a dict), oldest first."""
        cur = self._engine.con.cursor()
        try:
            result = cur.execute(
                "SELECT snapshot_id, snapshot_time, changes, author, commit_message, commit_extra_info "
                f"FROM {CATALOG}.snapshots() WHERE snapshot_id > ? ORDER BY snapshot_id",
                [-1 if since is None else since],
            )
            rows = []
            for snapshot_id, time, changes, author, message, extra in result.fetchall():
                rows.append({
                    "snapshot_id": int(snapshot_id),
                    "snapshot_time": time.astimezone(UTC) if time else None,
                    "changes": changes,
                    "author": author,
                    "commit_message": message,
                    "extra": json.loads(extra) if extra else {},
                })
            return rows
        finally:
            cur.close()

    # Reading --------------------------------------------------------------------------------------------------

    @property
    def fx_path(self) -> Path:
        return self.root / "reference" / "ecb_fx.parquet"

    def reference_path(self, name: str) -> Path:
        """File of one entity reference table (``gleif_lei``, ``firds_instruments``, ``openfigi_listings``)."""
        return self.root / "reference" / REFERENCE_TABLES[name][0]

    def connect(self, *, as_of: int | str | None = None) -> duckdb.DuckDBPyConnection:
        """A cursor exposing silver_<dataset>, fx_rates, reference tables and the gold views as of one snapshot.

        ``as_of`` defaults to the current snapshot, so every query on the cursor sees the same data. Cursors derived
        from the returned one with ``.cursor()`` do not inherit its search path: call ``connect`` again instead.
        """
        engine = self._engine
        current = self.current_snapshot()
        snapshot = current if as_of is None else int(as_of)
        if not engine.first_snapshot <= snapshot <= current:
            raise UnknownSnapshot(f"snapshot {as_of} does not exist (current: {current})")
        schema = engine.views(snapshot, self.fx_path)
        cur = engine.con.cursor()
        cur.execute(f"SET search_path = 'memory.{schema}'")
        return cur

    def export(self, out_dir: Path | str) -> dict[str, int]:
        """Write every gold table to Parquet (the publishable artefact); returns row counts."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        con = self.connect()
        counts: dict[str, int] = {}
        try:
            for dataset in Dataset:
                target = _quote(str(out / f"{dataset.value}.parquet"))
                con.execute(f"COPY (SELECT * FROM {dataset.value}) TO {target} (FORMAT parquet, COMPRESSION zstd)")
                counts[dataset.value] = con.execute(f"SELECT count(*) FROM {dataset.value}").fetchone()[0]
        finally:
            con.close()
        return counts

    # Migration and maintenance --------------------------------------------------------------------------------

    def migrate(self) -> dict[str, int]:
        """Load the Parquet silver files of the pre-DuckLake layout, one commit per file; safe to re-run.

        Files already migrated (recorded in each commit's extra info) are skipped. Returns rows loaded per dataset.
        """
        done = {s["extra"].get("migrated_from") for s in self.snapshots()} - {None}
        loaded = {dataset.value: 0 for dataset in Dataset}
        for dataset in Dataset:
            schema = arrow_schema(RECORD_TYPES[dataset])
            for path in sorted((self.silver / dataset.value).glob("*/*.parquet")):
                relative = str(path.relative_to(self.root))
                if relative in done:
                    continue
                table = pq.read_table(path)
                table = pa.table(
                    {name: table[name] if name in table.column_names else pa.nulls(len(table)) for name in schema.names}
                ).cast(schema)
                source = path.parent.name
                self._commit(dataset, source, table, {"records": len(table), "migrated_from": relative})
                loaded[dataset.value] += len(table)
        return loaded

    def maintain(self) -> None:
        """Flush inlined rows to Parquet and merge small files. Snapshots are never expired: they are citable."""
        cur = self._engine.con.cursor()
        try:
            cur.execute(f"CALL ducklake_flush_inlined_data({_quote(CATALOG)})")
            cur.execute(f"CALL ducklake_merge_adjacent_files({_quote(CATALOG)})")
        finally:
            cur.close()

    def parsed_documents(self, source: str) -> set[tuple[str, str]]:
        """(sha256, parser) of the bronze documents already committed to silver for one source (from commit info)."""
        return {
            (s["extra"]["sha256"], s["extra"].get("parser", ""))
            for s in self.snapshots()
            if s["extra"].get("source") == source and s["extra"].get("sha256")
        }
