"""Data-quality report of a release: row counts, freshness, identifier validity, duplicates and privacy checks."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

import duckdb

from ..models import Dataset
from ..normalize import valid_isin, valid_lei

STALE_AFTER_DAYS = 7
"""A source whose newest publication is older than this is flagged stale (sources publish every business day)."""

IDENTIFIERS: dict[Dataset, dict[str, str]] = {
    Dataset.INSIDER_TRANSACTIONS: {"isin": "isin", "issuer_lei": "lei"},
    Dataset.NET_SHORT_POSITIONS: {"isin": "isin", "issuer_lei": "lei", "holder_lei": "lei"},
}

BUSINESS_KEYS: dict[Dataset, list[str]] = {
    # Two gold rows with the same values here are probably one transaction reported twice (e.g. the same filing
    # published under two record ids). Record ids are unique by construction and checked separately.
    Dataset.INSIDER_TRANSACTIONS: [
        "source", "issuer_name", "pdmr_id", "transaction_date", "nature", "isin", "volume", "price", "published_at",
    ],
    Dataset.NET_SHORT_POSITIONS: ["source", "holder_name", "issuer_name", "isin", "position_date", "position_pct"],
}

PRIVACY_CHECKS: dict[Dataset, tuple[str, str]] = {
    # Rows that would expose a natural person's name. A release with any of these is refused.
    Dataset.INSIDER_TRANSACTIONS: (
        "notifier_name_of_natural_person",
        "notifier_name IS NOT NULL AND NOT notifier_is_legal_entity",
    ),
    Dataset.NET_SHORT_POSITIONS: (
        "natural_person_holder_not_pseudonymized",
        "holder_is_natural_person AND NOT starts_with(holder_name, 'p_')",
    ),
}

_CHECKERS = {"isin": valid_isin, "lei": valid_lei}


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _freshness(con: duckdb.DuckDBPyConnection, dataset: Dataset, now: datetime) -> list[dict[str, Any]]:
    data_date = "transaction_date" if dataset is Dataset.INSIDER_TRANSACTIONS else "position_date"
    rows = _rows(con, f"""
        SELECT source, count(*) AS rows, max(published_at) AS last_published_at, max({data_date}) AS last_data_date,
               max(fetched_at) AS last_fetched_at
        FROM {dataset.value} GROUP BY source ORDER BY source
    """)
    for row in rows:
        newest = row["last_published_at"] or row["last_fetched_at"]
        row["age_days"] = (now - newest).days if newest else None
        row["stale"] = row["age_days"] is None or row["age_days"] > STALE_AFTER_DAYS
    return rows


def _validity(con: duckdb.DuckDBPyConnection, dataset: Dataset) -> list[dict[str, Any]]:
    out = []
    for column, kind in IDENTIFIERS[dataset].items():
        counts: dict[str, dict[str, int]] = defaultdict(lambda: {"rows": 0, "present": 0, "valid": 0})
        values = con.execute(f"SELECT source, {column}, count(*) FROM {dataset.value} GROUP BY ALL").fetchall()
        for source, value, n in values:
            entry = counts[source]
            entry["rows"] += n
            if value is not None and value != "":
                entry["present"] += n
                entry["valid"] += n if _CHECKERS[kind](value) == value else 0
        for source in sorted(counts):
            entry = counts[source]
            out.append({
                "source": source,
                "column": column,
                **entry,
                "present_rate": round(entry["present"] / entry["rows"], 4) if entry["rows"] else None,
                "valid_rate": round(entry["valid"] / entry["present"], 4) if entry["present"] else None,
            })
    return out


def _duplicates(con: duckdb.DuckDBPyConnection, dataset: Dataset) -> dict[str, int]:
    key = ", ".join(BUSINESS_KEYS[dataset])
    duplicate_ids = con.execute(
        f"SELECT count(*) FROM (SELECT record_id FROM {dataset.value} GROUP BY 1 HAVING count(*) > 1)"
    ).fetchone()[0]
    groups, rows = con.execute(
        f"SELECT count(*), coalesce(sum(n), 0) FROM (SELECT count(*) AS n FROM {dataset.value} GROUP BY {key} "
        "HAVING count(*) > 1)"
    ).fetchone()
    return {"duplicate_record_ids": int(duplicate_ids), "business_key_groups": int(groups),
            "business_key_rows": int(rows)}


def quality_report(con: duckdb.DuckDBPyConnection, now: datetime) -> dict[str, Any]:
    """Quality metrics of the gold tables visible on ``con`` (one snapshot), per dataset and source."""
    datasets: dict[str, Any] = {}
    for dataset in Dataset:
        name, condition = PRIVACY_CHECKS[dataset]
        datasets[dataset.value] = {
            "rows": con.execute(f"SELECT count(*) FROM {dataset.value}").fetchone()[0],
            "freshness": _freshness(con, dataset, now),
            "identifiers": _validity(con, dataset),
            "duplicates": _duplicates(con, dataset),
            "privacy": {name: con.execute(f"SELECT count(*) FROM {dataset.value} WHERE {condition}").fetchone()[0]},
        }
    return {"generated_at": now, "stale_after_days": STALE_AFTER_DAYS, "datasets": datasets}


def privacy_violations(report: dict[str, Any]) -> dict[str, int]:
    return {
        f"{dataset}.{check}": n
        for dataset, body in report["datasets"].items()
        for check, n in body["privacy"].items()
        if n
    }


def _pct(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate:.1%}"


def _date(value: Any) -> str:
    return "n/a" if value is None else (value.date().isoformat() if isinstance(value, datetime) else str(value))


def quality_markdown(report: dict[str, Any], *, version: str, snapshot_id: int) -> str:
    lines = [
        f"# Data-quality report, release {version}",
        "",
        f"DuckLake snapshot {snapshot_id}, generated {report['generated_at']:%Y-%m-%d %H:%M} UTC. "
        f"A source is stale when its newest publication is more than {report['stale_after_days']} days old.",
    ]
    for dataset, body in report["datasets"].items():
        lines += ["", f"## `{dataset}`: {body['rows']:,} rows", "", "### Freshness", "",
                  "| Source | Rows | Last published | Last data date | Last fetched | Age (days) | Status |",
                  "|---|---:|---|---|---|---:|---|"]
        for row in body["freshness"]:
            lines.append(
                f"| `{row['source']}` | {row['rows']:,} | {_date(row['last_published_at'])} | "
                f"{_date(row['last_data_date'])} | {_date(row['last_fetched_at'])} | "
                f"{'n/a' if row['age_days'] is None else row['age_days']} | {'stale' if row['stale'] else 'fresh'} |"
            )
        lines += ["", "### Identifiers (ISO 6166 ISIN and ISO 17442 LEI check digits)", "",
                  "| Source | Column | Present | Valid when present |", "|---|---|---:|---:|"]
        for row in body["identifiers"]:
            lines.append(
                f"| `{row['source']}` | `{row['column']}` | {_pct(row['present_rate'])} | {_pct(row['valid_rate'])} |"
            )
        dup = body["duplicates"]
        privacy = ", ".join(f"`{name}`: {n}" for name, n in body["privacy"].items())
        lines += ["", "### Duplicates and privacy", "",
                  f"- Duplicate record ids: {dup['duplicate_record_ids']}",
                  f"- Rows sharing a business key ({', '.join(f'`{k}`' for k in BUSINESS_KEYS[Dataset(dataset)])}):"
                  f" {dup['business_key_rows']} in {dup['business_key_groups']} groups",
                  f"- Privacy checks (must be 0): {privacy}"]
    return "\n".join(lines) + "\n"
