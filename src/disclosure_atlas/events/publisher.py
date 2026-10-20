"""Change-feed publisher: new gold rows -> atlas.filings.v1, each run -> atlas.snapshots.v1.

No dual writes (docs/decisions/0011): the lake is committed first, then this publisher reads the gold rows
whose stream position `(first_seen_at, record_id)` is after the stored checkpoint, publishes them, waits for
the broker's acknowledgements and only then moves the checkpoint. A crash can re-publish, never lose; the
event id is the record id, so consumers deduplicate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..api.schemas import TX_COLUMNS
from ..lake import Lake
from ..models import Dataset
from .bus import Bus
from .contracts import FILING_PUBLISHED, SNAPSHOT_COMMITTED, FilingPublished, SnapshotCommitted, headers

PAGE_SIZE = 1_000


def stream_id(first_seen_at: datetime, record_id: str) -> str:
    """Position of a filing in the live stream: the SSE event id (same format as the Python API)."""
    return f"{first_seen_at.isoformat()}|{record_id}"


def parse_stream_id(value: str) -> tuple[datetime, str]:
    seen, record = value.split("|", 1)
    return datetime.fromisoformat(seen), record


@dataclass
class Checkpoint:
    position: str | None = None  # stream id of the last published filing
    snapshot: str | None = None  # lake version read by the last run
    updated_at: str | None = None


@dataclass
class RunReport:
    filings: int
    snapshot: str
    checkpoint: str | None

    def summary(self) -> str:
        return f"published {self.filings} filings (snapshot {self.snapshot}, checkpoint {self.checkpoint})"


class ChangeFeedPublisher:
    def __init__(self, lake: Lake, bus: Bus, checkpoint_path: Path | None = None) -> None:
        self.lake = lake
        self.bus = bus
        self.checkpoint_path = checkpoint_path or lake.root / "events" / "checkpoint.json"

    # Checkpoint ---------------------------------------------------------------------------------------------
    def load_checkpoint(self) -> Checkpoint | None:
        if not self.checkpoint_path.exists():
            return None
        return Checkpoint(**json.loads(self.checkpoint_path.read_text(encoding="utf-8")))

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.updated_at = datetime.now(UTC).isoformat()
        tmp = self.checkpoint_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(checkpoint.__dict__, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.checkpoint_path)  # atomic: a crash leaves the old or the new checkpoint, never half

    def start_at_end(self) -> Checkpoint:
        """Initialise a missing checkpoint at the newest gold row, so history is not re-published.
        Use `backfill=True` on `publish` to replay everything instead."""
        if (existing := self.load_checkpoint()) is not None:
            return existing
        con = self.lake.connect()
        row = con.execute(
            "SELECT first_seen_at, record_id FROM insider_transactions "
            "ORDER BY first_seen_at DESC, record_id DESC LIMIT 1"
        ).fetchone()
        checkpoint = Checkpoint(position=stream_id(row[0], row[1]) if row else None, snapshot=self.lake.version())
        self.save_checkpoint(checkpoint)
        return checkpoint

    # Publishing ---------------------------------------------------------------------------------------------
    def _page(self, con: Any, position: str | None) -> list[dict[str, Any]]:
        where, params = "", []
        if position:
            seen, record = parse_stream_id(position)
            where = "WHERE first_seen_at > ? OR (first_seen_at = ? AND record_id > ?)"
            params = [seen, seen, record]
        result = con.execute(
            f"SELECT {TX_COLUMNS} FROM insider_transactions {where} ORDER BY first_seen_at, record_id LIMIT ?",
            [*params, PAGE_SIZE],
        )
        columns = [d[0] for d in result.description]
        return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]

    def publish(self, backfill: bool = False) -> RunReport:
        checkpoint = (self.load_checkpoint() or Checkpoint()) if backfill else self.start_at_end()
        previous_snapshot = checkpoint.snapshot
        snapshot = self.lake.version()
        con = self.lake.connect()
        total = 0
        while rows := self._page(con, checkpoint.position):
            for row in rows:
                event = FilingPublished(**row)
                self.bus.produce(
                    FILING_PUBLISHED.topic,
                    key=(event.issuer_lei or event.id).encode(),
                    value=event.model_dump_json().encode(),
                    headers=headers(FILING_PUBLISHED, event.id, event.first_seen_at, subject=event.issuer_lei),
                )
            self.bus.flush()  # acknowledged by the broker before the checkpoint moves
            checkpoint.position = stream_id(rows[-1]["first_seen_at"], rows[-1]["id"])
            self.save_checkpoint(checkpoint)
            total += len(rows)

        committed = SnapshotCommitted(
            dataset=Dataset.INSIDER_TRANSACTIONS.value,
            snapshot=snapshot,
            previous_snapshot=previous_snapshot,
            committed_at=datetime.now(UTC),
            filings_published=total,
            checkpoint=checkpoint.position,
        )
        self.bus.produce(
            SNAPSHOT_COMMITTED.topic,
            key=committed.dataset.encode(),
            value=committed.model_dump_json().encode(),
            headers=headers(SNAPSHOT_COMMITTED, f"{committed.dataset}:{snapshot}:{committed.committed_at.timestamp()}",
                            committed.committed_at, subject=committed.dataset),
        )
        self.bus.flush()
        checkpoint.snapshot = snapshot
        self.save_checkpoint(checkpoint)
        return RunReport(filings=total, snapshot=snapshot, checkpoint=checkpoint.position)


def from_env(lake: Lake, environ: dict[str, str]) -> ChangeFeedPublisher | None:
    """Publisher configured by ATLAS_KAFKA_BOOTSTRAP (and optional ATLAS_SCHEMA_REGISTRY_URL), or None."""
    bootstrap = environ.get("ATLAS_KAFKA_BOOTSTRAP")
    if not bootstrap:
        return None
    from .bus import KafkaBus

    if registry_url := environ.get("ATLAS_SCHEMA_REGISTRY_URL"):
        from .registry import SchemaRegistry

        SchemaRegistry(registry_url).register_all()
    return ChangeFeedPublisher(lake, KafkaBus(bootstrap))
