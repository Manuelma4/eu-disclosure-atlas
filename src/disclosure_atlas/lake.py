"""File-based medallion lake (see docs/decisions/0001).

bronze/<source>/YYYY/MM/<ts>-<sha>.<ext>   raw responses, immutable, never published
bronze/<source>/_manifest.jsonl            provenance of every raw document
silver/<dataset>/<source>/<ts>-<id>.parquet typed records, append-only
reference/<name>.parquet                   reference data (e.g. ECB euro reference rates)
gold                                        DuckDB views: latest version of each record, minus superseded ones,
                                            plus derived columns (first_seen_at, is_open_market, amount_eur...)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .connectors.base import RawDocument, Record
from .models import RECORD_TYPES, Dataset, arrow_schema
from .privacy import load_key

_LATEST = """
    SELECT * EXCLUDE (_rn) FROM (
        SELECT *,
               row_number() OVER (PARTITION BY record_id ORDER BY fetched_at DESC) AS _rn,
               min(fetched_at) OVER (PARTITION BY record_id) AS first_seen_at
        FROM silver_{dataset}
    )
    WHERE _rn = 1 AND NOT is_superseded
"""


def _gold_sql(dataset: Dataset, has_fx: bool) -> str:
    latest = _LATEST.format(dataset=dataset.value)
    if dataset is not Dataset.INSIDER_TRANSACTIONS:
        return latest
    amount_eur = (
        "CASE WHEN t.currency = 'EUR' THEN t.amount ELSE t.amount / fx.rate END"
        if has_fx
        else "CAST(NULL AS DOUBLE)"
    )
    fx_join = (
        "ASOF LEFT JOIN fx_rates fx ON t.currency = fx.currency AND t.transaction_date >= fx.date"
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


class Lake:
    def __init__(self, root: Path | str = "lake") -> None:
        self.root = Path(root)
        self.bronze = self.root / "bronze"
        self.silver = self.root / "silver"

    def pseudonym_key(self) -> bytes:
        return load_key(self.root)

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

    def write_silver(self, dataset: Dataset, source: str, records: Sequence[Record]) -> Path | None:
        if not records:
            return None
        schema = arrow_schema(RECORD_TYPES[dataset])
        table = pa.Table.from_pylist([r.model_dump() for r in records], schema=schema)
        folder = self.silver / dataset.value / source
        folder.mkdir(parents=True, exist_ok=True)
        batch = hashlib.sha256("".join(r.record_id for r in records).encode()).hexdigest()[:12]
        path = folder / f"{records[0].fetched_at:%Y%m%dT%H%M%S%fZ}-{batch}.parquet"
        pq.write_table(table, path, compression="zstd")
        return path

    @property
    def fx_path(self) -> Path:
        return self.root / "reference" / "ecb_fx.parquet"

    def version(self) -> str:
        """Cheap content version of the lake: changes whenever a silver or reference file changes."""
        digest = hashlib.sha1()
        for path in sorted(self.silver.glob("*/*/*.parquet")) + sorted(self.root.glob("reference/*.parquet")):
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns};".encode())
        return digest.hexdigest()[:12]

    def connect(self, database: str = ":memory:") -> duckdb.DuckDBPyConnection:
        """DuckDB connection exposing silver_<dataset> views, gold <dataset> views and fx_rates."""
        con = duckdb.connect(database)
        con.execute("SET TimeZone = 'UTC'")
        for dataset in Dataset:
            folder = self.silver / dataset.value
            if any(folder.glob("*/*.parquet")):
                pattern = str(folder / "*" / "*.parquet").replace("'", "''")
                con.execute(
                    f"CREATE OR REPLACE VIEW silver_{dataset.value} AS "
                    f"SELECT * FROM read_parquet('{pattern}', union_by_name = true)"
                )
            else:
                # A real (empty) table, so cursors derived from this connection can see it.
                con.register("_empty", arrow_schema(RECORD_TYPES[dataset]).empty_table())
                con.execute(f"CREATE OR REPLACE TABLE silver_{dataset.value} AS SELECT * FROM _empty")
                con.unregister("_empty")
        has_fx = self.fx_path.exists()
        if has_fx:
            fx = str(self.fx_path).replace("'", "''")
            con.execute(f"CREATE OR REPLACE VIEW fx_rates AS SELECT * FROM read_parquet('{fx}')")
        for dataset in Dataset:
            con.execute(f"CREATE OR REPLACE VIEW {dataset.value} AS {_gold_sql(dataset, has_fx)}")
        return con

    def export(self, out_dir: Path | str) -> dict[str, int]:
        """Write every gold table to Parquet (the publishable artefact); returns row counts."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        con = self.connect()
        counts: dict[str, int] = {}
        for dataset in Dataset:
            target = str(out / f"{dataset.value}.parquet").replace("'", "''")
            con.execute(f"COPY (SELECT * FROM {dataset.value}) TO '{target}' (FORMAT parquet, COMPRESSION zstd)")
            counts[dataset.value] = con.execute(f"SELECT count(*) FROM {dataset.value}").fetchone()[0]
        return counts
