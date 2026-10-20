"""`snapshot.committed`: the signal that a new DuckLake snapshot is readable.

The orchestration layer reports every snapshot it commits through a ``SnapshotPublisher``. The default writes
JSON lines to ``<lake>/events/snapshots.jsonl``; the event-streaming workstream (P3) plugs in a broker-backed
publisher without touching this package, through ``ATLAS_SNAPSHOT_PUBLISHER=package.module:factory`` where
``factory(lake_root: Path) -> SnapshotPublisher``.

Delivery here is best effort (a run hook). The authoritative feed is the lake itself: ``Lake.snapshots(since=...)``
can replay every commit from a checkpoint.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotCommitted:
    snapshot_id: int
    committed_at: datetime | None
    dataset: str | None
    source: str | None
    records: int | None
    commit_message: str | None
    partition: str | None = None
    run_id: str | None = None

    @classmethod
    def from_lake(cls, snapshot: dict[str, Any], *, partition: str | None = None, run_id: str | None = None):
        extra = snapshot.get("extra") or {}
        return cls(
            snapshot_id=snapshot["snapshot_id"],
            committed_at=snapshot.get("snapshot_time"),
            dataset=extra.get("dataset"),
            source=extra.get("source"),
            records=extra.get("records"),
            commit_message=snapshot.get("commit_message"),
            partition=partition,
            run_id=run_id,
        )

    def to_json(self) -> str:
        return json.dumps({"type": "snapshot.committed", **asdict(self)}, default=str)


class SnapshotPublisher(Protocol):
    def publish(self, event: SnapshotCommitted) -> None: ...


class JsonlSnapshotPublisher:
    """Appends one JSON line per event: a local, inspectable stand-in for the event bus."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def publish(self, event: SnapshotCommitted) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as out:
            out.write(event.to_json() + "\n")


class LogSnapshotPublisher:
    def publish(self, event: SnapshotCommitted) -> None:
        log.info("snapshot.committed %s", event.to_json())


def publisher_from_env(lake_root: Path) -> SnapshotPublisher:
    spec = os.environ.get("ATLAS_SNAPSHOT_PUBLISHER", "jsonl")
    if spec == "jsonl":
        return JsonlSnapshotPublisher(lake_root / "events" / "snapshots.jsonl")
    if spec == "log":
        return LogSnapshotPublisher()
    module, _, attr = spec.partition(":")
    if not attr:
        raise ValueError(f"ATLAS_SNAPSHOT_PUBLISHER must be 'jsonl', 'log' or 'module:factory', not {spec!r}")
    return getattr(importlib.import_module(module), attr)(lake_root)
