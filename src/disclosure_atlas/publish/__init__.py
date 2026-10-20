"""Open-data releases (WS8): the gold tables of one DuckLake snapshot as files anyone can download and query.

<out>/
  insider_transactions.parquet          gold tables, Parquet (zstd)
  net_short_positions.parquet
  eu-disclosure-atlas.duckdb            the same tables plus ``sources`` (reuse terms) and ``release`` (metadata)
  datapackage.json                      Frictionless Data Package v2: schemas, sources, licences, snapshot id
  quality.json, quality.md              data-quality report
  badge.json                            freshness badge (shields.io endpoint format)
  SHA256SUMS                            checksums of every file above (``shasum -a 256 -c SHA256SUMS``)

Only gold is released: natural persons are already pseudonyms there, and the release is refused if a privacy
check finds a name that should not be there. Bronze (raw regulator files) is never read. The folder is flat, like
the assets of a GitHub Release, so SHA256SUMS verifies downloaded assets as they are.
"""

from __future__ import annotations

import enum
import hashlib
import json
import re
import typing
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from ..lake import Lake, _quote
from ..models import RECORD_TYPES, Dataset
from .quality import privacy_violations, quality_markdown, quality_report
from .sources import DOCS, ECB_FX, REPO, source_note_url, terms_for, terms_note

DUCKDB_FILE = "eu-disclosure-atlas.duckdb"
PACKAGE_NAME = "eu-disclosure-atlas"
PROFILE = "https://datapackage.org/profiles/2.0/datapackage.json"

_TABLE_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", date: "date", datetime: "datetime"}
_DUCKDB_TYPES = {"VARCHAR": "string", "BIGINT": "integer", "INTEGER": "integer", "DOUBLE": "number",
                 "BOOLEAN": "boolean", "DATE": "date", "TIMESTAMP WITH TIME ZONE": "datetime"}
GOLD_ONLY = {
    "first_seen_at": "Gold only: when the Atlas first fetched this record (first version)",
    "is_open_market": "Gold only: buy or sell of shares on a trading venue",
    "filing_lag_days": "Gold only: days from the transaction to its publication",
    "amount_eur": "Gold only: amount in euros at the ECB reference rate of the transaction date (or the last before)",
}


class PrivacyCheckFailed(RuntimeError):
    """A gold row would expose a natural person's name; nothing is released."""


@dataclass(frozen=True)
class Release:
    out: Path
    version: str
    snapshot_id: int
    rows: dict[str, int]
    files: dict[str, str]
    """Relative path -> SHA-256 of every released file except SHA256SUMS itself."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def field_descriptions(docs: Path = DOCS) -> dict[str, dict[str, str]]:
    """Column descriptions per dataset from docs/data-model.md ("As above" resolves to the previous dataset)."""
    path = docs / "data-model.md"
    if not path.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    previous: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if heading := re.match(r"## `(\w+)`", line):
            previous = current or previous
            current = out.setdefault(heading.group(1), {})
        elif current is not None and line.startswith("| `"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 3:
                continue
            for column in re.findall(r"`(\w+)`", cells[0]):
                text = previous.get(column, "") if cells[2] == "As above" else cells[2]
                if text:
                    current[column] = text
    return out


def _field_type(annotation: object) -> tuple[str, list[str] | None]:
    if typing.get_origin(annotation) is not None:
        (inner,) = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _field_type(inner)
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return "string", [member.value for member in annotation]
    return _TABLE_TYPES[annotation], None  # type: ignore[index]


def table_schema(con: duckdb.DuckDBPyConnection, dataset: Dataset, descriptions: dict[str, str]) -> dict[str, Any]:
    """Frictionless Table Schema: model fields (types and enums from the Pydantic model), then gold-only columns."""
    model = RECORD_TYPES[dataset]
    fields: list[dict[str, Any]] = []
    columns = con.execute(f"SELECT column_name, column_type FROM (DESCRIBE {dataset.value})").fetchall()
    for name, duck_type in columns:
        if name in model.model_fields:
            info = model.model_fields[name]
            kind, values = _field_type(info.annotation)
            field: dict[str, Any] = {"name": name, "type": kind}
            constraints: dict[str, Any] = {}
            if info.is_required() and typing.get_origin(info.annotation) is None:
                constraints["required"] = True
            if values:
                constraints["enum"] = values
            if constraints:
                field["constraints"] = constraints
        else:
            field = {"name": name, "type": _DUCKDB_TYPES.get(duck_type, "any")}
        if text := descriptions.get(name) or GOLD_ONLY.get(name):
            field["description"] = text
        fields.append(field)
    return {"fields": fields, "primaryKey": ["record_id"]}


def _sources(con: duckdb.DuckDBPyConnection) -> list[str]:
    return sorted({
        source
        for dataset in Dataset
        for (source,) in con.execute(f"SELECT DISTINCT source FROM {dataset.value}").fetchall()
    })


def _source_entry(source: str) -> dict[str, Any]:
    terms = terms_for(source)
    entry: dict[str, Any] = {"title": terms.title, "path": source_note_url(source), "atlas:source": source,
                             "atlas:licence": terms.licence, "atlas:attribution": terms.attribution,
                             "atlas:commercialUse": terms.commercial_use}
    if terms.licence_url:
        entry["atlas:licenceUrl"] = terms.licence_url
    if note := terms_note(source):
        entry["atlas:terms"] = note
    return entry


def _licence(source: str) -> dict[str, Any]:
    terms = terms_for(source)
    licence: dict[str, Any] = {"title": f"{source}: {terms.licence}"}
    if re.fullmatch(r"[a-z0-9.-]+", terms.licence):
        licence["name"] = terms.licence
    if terms.licence_url:
        licence["path"] = terms.licence_url
    return licence


def datapackage(con: duckdb.DuckDBPyConnection, *, version: str, snapshot_id: int, snapshot_time: datetime | None,
                created: datetime, files: dict[str, str], out: Path) -> dict[str, Any]:
    descriptions = field_descriptions()
    sources = _sources(con)
    resources = []
    for dataset in Dataset:
        path = f"{dataset.value}.parquet"
        resource_sources = [
            s for (s,) in con.execute(f"SELECT DISTINCT source FROM {dataset.value} ORDER BY 1").fetchall()
        ]
        resources.append({
            "name": dataset.value,
            "type": "table",
            "path": path,
            "format": "parquet",
            "mediatype": "application/vnd.apache.parquet",
            "encoding": "binary",
            "bytes": (out / path).stat().st_size,
            "hash": f"sha256:{files[path]}",
            "description": (RECORD_TYPES[dataset].__doc__ or "").strip(),
            "schema": table_schema(con, dataset, descriptions.get(dataset.value, {})),
            "sources": [{"title": terms_for(s).title, "path": source_note_url(s)} for s in resource_sources],
        })
    resources.append({
        "name": "atlas_duckdb",
        "path": DUCKDB_FILE,
        "format": "duckdb",
        "mediatype": "application/vnd.duckdb",
        "encoding": "binary",
        "bytes": (out / DUCKDB_FILE).stat().st_size,
        "hash": f"sha256:{files[DUCKDB_FILE]}",
        "description": "The same tables in one DuckDB database, plus `sources` (reuse terms) and `release`.",
    })
    return {
        "$schema": PROFILE,
        "name": PACKAGE_NAME,
        "title": "EU Disclosure Atlas: European insider trades (MAR Art. 19) and net short positions (SSR Art. 6)",
        "description": (
            "Unified, pseudonymized records of managers' transactions and public net short positions published by "
            "European market authorities. Natural persons appear only as keyed per-issuer pseudonyms. "
            "Not investment advice. Reuse is subject to each source's terms: see `sources` and `licenses`."
        ),
        "homepage": REPO,
        "version": version,
        "created": created.isoformat(timespec="seconds"),
        "keywords": ["MAR", "SSR", "insider trading", "short selling", "PDMR", "open data", "Europe"],
        "contributors": [
            {"title": "Manuel David Maya Rosero", "path": "https://manuelma4.github.io/", "roles": ["creator"]}
        ],
        "licenses": [_licence(s) for s in sources] + [{"title": f"amount_eur: {ECB_FX.licence}",
                                                        "path": ECB_FX.licence_url}],
        "sources": [_source_entry(s) for s in sources] + [{"title": ECB_FX.title, "path": ECB_FX.licence_url,
                                                           "atlas:attribution": ECB_FX.attribution}],
        "resources": resources,
        "atlas:snapshotId": snapshot_id,
        "atlas:snapshotTime": snapshot_time.isoformat(timespec="seconds") if snapshot_time else None,
        "atlas:qualityReport": "quality.md",
    }


def _write_duckdb(path: Path, out: Path, *, version: str, snapshot_id: int, created: datetime,
                  sources: list[str]) -> None:
    path.unlink(missing_ok=True)
    con = duckdb.connect(str(path))
    try:
        for dataset in Dataset:
            parquet = _quote(str(out / f"{dataset.value}.parquet"))
            con.execute(f"CREATE TABLE {dataset.value} AS SELECT * FROM read_parquet({parquet})")
        con.execute("CREATE TABLE sources (source VARCHAR, title VARCHAR, licence VARCHAR, licence_url VARCHAR, "
                    "attribution VARCHAR, commercial_use BOOLEAN, note VARCHAR)")
        for source in sources:
            terms = terms_for(source)
            con.execute("INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?)", [
                source, terms.title, terms.licence, terms.licence_url, terms.attribution, terms.commercial_use,
                source_note_url(source)])
        con.execute("CREATE TABLE release AS SELECT ? AS version, ? AS snapshot_id, ? AS created_at, ? AS homepage",
                    [version, snapshot_id, created, REPO])
        con.execute("CHECKPOINT")
    finally:
        con.close()


def publish(lake: Lake, out_dir: Path | str, *, as_of: int | None = None, now: datetime | None = None) -> Release:
    """Write an open-data release of the gold tables as of one snapshot (default: the current one)."""
    out = Path(out_dir)
    created = (now or datetime.now(UTC)).astimezone(UTC)
    con = lake.connect(as_of=as_of)
    try:
        snapshot_id = int(as_of) if as_of is not None else lake.current_snapshot()
        snapshot_time = next((s["snapshot_time"] for s in lake.snapshots(since=snapshot_id - 1)
                              if s["snapshot_id"] == snapshot_id), None)
        version = f"{created:%Y.%m.%d}-s{snapshot_id}"

        report = quality_report(con, created)
        if violations := privacy_violations(report):
            raise PrivacyCheckFailed(f"release refused, privacy checks failed: {violations}")

        out.mkdir(parents=True, exist_ok=True)
        rows: dict[str, int] = {}
        for dataset in Dataset:
            target = _quote(str(out / f"{dataset.value}.parquet"))
            con.execute(f"COPY (SELECT * FROM {dataset.value} ORDER BY record_id) TO {target} "
                        "(FORMAT parquet, COMPRESSION zstd)")
            rows[dataset.value] = report["datasets"][dataset.value]["rows"]
        sources = _sources(con)
        _write_duckdb(out / DUCKDB_FILE, out, version=version, snapshot_id=snapshot_id, created=created,
                      sources=sources)

        (out / "quality.json").write_text(
            json.dumps({"version": version, "snapshot_id": snapshot_id, **report}, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        (out / "quality.md").write_text(quality_markdown(report, version=version, snapshot_id=snapshot_id),
                                        encoding="utf-8")
        stale = [row["source"] for body in report["datasets"].values() for row in body["freshness"] if row["stale"]]
        (out / "badge.json").write_text(json.dumps({
            "schemaVersion": 1, "label": "open data", "message": f"{created:%Y-%m-%d}",
            "color": "orange" if stale else "brightgreen",
        }) + "\n", encoding="utf-8")

        payload = [f"{d.value}.parquet" for d in Dataset] + [DUCKDB_FILE, "quality.json", "quality.md",
                                                                  "badge.json"]
        files = {name: sha256_file(out / name) for name in payload}
        package = datapackage(con, version=version, snapshot_id=snapshot_id, snapshot_time=snapshot_time,
                              created=created, files=files, out=out)
    finally:
        con.close()
    (out / "datapackage.json").write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    files["datapackage.json"] = sha256_file(out / "datapackage.json")
    (out / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(files.items())),
                                    encoding="utf-8")
    return Release(out=out, version=version, snapshot_id=snapshot_id, rows=rows, files=files)
