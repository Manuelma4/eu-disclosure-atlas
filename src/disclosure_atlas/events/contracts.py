"""Event contracts: topics, CloudEvents attributes and payload JSON Schemas (see docs/decisions/0011).

Events travel in CloudEvents 1.0 *binary* mode on Kafka: the attributes are `ce_*` record headers and the
record value is the JSON payload, so consumers read plain JSON and the schema registry validates exactly
the payload. Payload schemas are generated from the Pydantic models below and committed in
`contracts/events/` (`disclosure-atlas contracts`); CI fails when they drift from the code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..api.schemas import Transaction

SPEC_VERSION = "1.0"
SOURCE = "/disclosure-atlas/ingestion"
SCHEMA_BASE_URL = "https://github.com/Manuelma4/eu-disclosure-atlas/blob/main/contracts/events/"


class FilingPublished(Transaction):
    """A gold insider transaction seen for the first time. Same fields as the REST API's `Transaction`,
    so the SSE stream can forward the payload unchanged."""


class SnapshotCommitted(BaseModel):
    """One change-feed run over a dataset: the lake version it read and what it published."""

    dataset: str = Field(description="Gold dataset, e.g. insider_transactions")
    snapshot: str = Field(description="Lake version the run read (the API's X-Atlas-Snapshot)")
    previous_snapshot: str | None = Field(description="Version read by the previous run, if any")
    committed_at: datetime = Field(description="When the run finished publishing (UTC)")
    filings_published: int = Field(description="Number of filing.published events in this run")
    checkpoint: str | None = Field(description="Stream position after this run (`<first_seen_at>|<record id>`)")


@dataclass(frozen=True)
class EventType:
    name: str  # contract file stem, e.g. filing-published.v1
    type: str  # CloudEvents `type`
    topic: str
    model: type[BaseModel]

    @property
    def subject(self) -> str:
        """Schema registry subject (TopicNameStrategy)."""
        return f"{self.topic}-value"

    @property
    def schema_url(self) -> str:
        return f"{SCHEMA_BASE_URL}{self.name}.json"


FILING_PUBLISHED = EventType(
    "filing-published.v1", "eu.disclosure-atlas.filing.published.v1", "atlas.filings.v1", FilingPublished
)
SNAPSHOT_COMMITTED = EventType(
    "snapshot-committed.v1", "eu.disclosure-atlas.snapshot.committed.v1", "atlas.snapshots.v1", SnapshotCommitted
)
EVENT_TYPES = (FILING_PUBLISHED, SNAPSHOT_COMMITTED)


def json_schema(event: EventType) -> dict[str, Any]:
    schema = event.model.model_json_schema(mode="serialization")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": event.schema_url,
        "x-cloudevents-type": event.type,
        "x-kafka-topic": event.topic,
        **schema,
    }


def render(event: EventType) -> str:
    return json.dumps(json_schema(event), indent=2, sort_keys=False) + "\n"


def write_contracts(out_dir: Path | str) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for event in EVENT_TYPES:
        path = out / f"{event.name}.json"
        path.write_text(render(event), encoding="utf-8")
        paths.append(path)
    return paths


def stale_contracts(out_dir: Path | str) -> list[str]:
    """Contract files that are missing or differ from the code (empty when up to date)."""
    out = Path(out_dir)
    return [
        event.name
        for event in EVENT_TYPES
        if not (out / f"{event.name}.json").exists()
        or (out / f"{event.name}.json").read_text(encoding="utf-8") != render(event)
    ]


def headers(event: EventType, event_id: str, time: datetime, subject: str | None = None) -> list[tuple[str, bytes]]:
    """CloudEvents binary-mode Kafka headers (CloudEvents Kafka protocol binding)."""
    values = {
        "ce_specversion": SPEC_VERSION,
        "ce_id": event_id,
        "ce_source": SOURCE,
        "ce_type": event.type,
        "ce_time": time.isoformat().replace("+00:00", "Z"),
        "ce_dataschema": event.schema_url,
        "content-type": "application/json",
    }
    if subject:
        values["ce_subject"] = subject
    return [(k, v.encode()) for k, v in values.items()]
