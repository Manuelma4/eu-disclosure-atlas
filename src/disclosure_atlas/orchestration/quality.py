"""Data-quality measures behind the Dagster asset checks. Pure functions over a lake cursor, so they are testable
without Dagster and reusable by reports."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb

from ..models import Dataset
from ..normalize import valid_isin, valid_lei

VALIDITY_THRESHOLD = 0.98
SUPERSEDED_THRESHOLD = 0.25
VOLUME_WINDOW_DAYS = 28
VOLUME_FACTOR = 10.0

_DATE_COLUMN = {Dataset.INSIDER_TRANSACTIONS: "published_at", Dataset.NET_SHORT_POSITIONS: "published_at"}
_LEI_COLUMNS = {Dataset.INSIDER_TRANSACTIONS: ["issuer_lei"], Dataset.NET_SHORT_POSITIONS: ["issuer_lei", "holder_lei"]}


@dataclass
class Measure:
    passed: bool
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


def freshness(con: duckdb.DuckDBPyConnection, dataset: Dataset, source: str, max_hours: float,
              now: datetime | None = None) -> Measure:
    """The newest publication of the source is at most ``max_hours`` old."""
    now = now or datetime.now(UTC)
    newest, last_seen = con.execute(
        f"SELECT max(published_at), max(first_seen_at) FROM {dataset.value} WHERE source = ?", [source]
    ).fetchone()
    if newest is None:
        return Measure(False, "no published records yet", {"max_staleness_hours": max_hours})
    age = (now - newest).total_seconds() / 3600
    return Measure(
        age <= max_hours,
        f"newest publication is {age:.1f} h old (limit {max_hours:g} h)",
        {"newest_published_at": newest.isoformat(), "last_ingested_at": last_seen.isoformat() if last_seen else None,
         "age_hours": round(age, 2), "max_staleness_hours": max_hours},
    )


def volume(con: duckdb.DuckDBPyConnection, dataset: Dataset, source: str, today: date | None = None) -> Measure:
    """The last complete weekday's publication count is within a factor of the trailing 28-day weekday median."""
    today = today or datetime.now(UTC).date()
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    start = day - timedelta(days=VOLUME_WINDOW_DAYS)
    counts = dict(con.execute(
        f"SELECT CAST(timezone('UTC', {_DATE_COLUMN[dataset]}) AS DATE) AS d, count(*) FROM {dataset.value} "
        "WHERE source = ? AND d BETWEEN ? AND ? GROUP BY 1",
        [source, start, day],
    ).fetchall())
    history = [
        counts.get(start + timedelta(days=i), 0)
        for i in range(VOLUME_WINDOW_DAYS)
        if (start + timedelta(days=i)).weekday() < 5
    ]
    median = statistics.median(history) if history else 0.0
    count = counts.get(day, 0)
    passed = median < 1 or median / VOLUME_FACTOR <= count <= median * VOLUME_FACTOR
    return Measure(
        passed,
        f"{count} records published on {day} vs a weekday median of {median:g}",
        {"day": day.isoformat(), "count": count, "median_28d": median, "factor": VOLUME_FACTOR},
    )


def identifier_validity(con: duckdb.DuckDBPyConnection, dataset: Dataset, source: str) -> Measure:
    """Share of non-empty ISINs and LEIs in gold that pass their check digits (ISO 6166, ISO 17442)."""
    metadata: dict[str, Any] = {"threshold": VALIDITY_THRESHOLD}
    rates = []
    for column, validator in [("isin", valid_isin)] + [(c, valid_lei) for c in _LEI_COLUMNS[dataset]]:
        rows = con.execute(
            f"SELECT {column}, count(*) FROM {dataset.value} WHERE source = ? AND coalesce({column}, '') <> '' "
            "GROUP BY 1",
            [source],
        ).fetchall()
        total = sum(n for _, n in rows)
        valid = sum(n for value, n in rows if validator(value))
        rate = valid / total if total else 1.0
        rates.append(rate)
        metadata |= {f"{column}_values": total, f"{column}_valid_rate": round(rate, 4)}
    worst = min(rates)
    return Measure(worst >= VALIDITY_THRESHOLD, f"lowest identifier validity rate {worst:.1%}", metadata)


def superseded_share(con: duckdb.DuckDBPyConnection, dataset: Dataset, source: str) -> Measure:
    """Share of records whose latest version is superseded (revoked or replaced) by the authority."""
    total, superseded = con.execute(
        f"""
        SELECT count(*), count(*) FILTER (WHERE is_superseded) FROM (
            SELECT arg_max(is_superseded, fetched_at) AS is_superseded
            FROM silver_{dataset.value} WHERE source = ? GROUP BY record_id
        )
        """,
        [source],
    ).fetchone()
    share = superseded / total if total else 0.0
    return Measure(
        share <= SUPERSEDED_THRESHOLD,
        f"{share:.1%} of {total} records are superseded (limit {SUPERSEDED_THRESHOLD:.0%})",
        {"records": total, "superseded": superseded, "share": round(share, 4), "threshold": SUPERSEDED_THRESHOLD},
    )
