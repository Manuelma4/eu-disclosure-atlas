"""Change-feed publisher, event contracts and schema registry, offline (fake bus, mocked registry)."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import EXAMPLE_LEI, fi_doc, fi_row

from disclosure_atlas.api.schemas import Transaction
from disclosure_atlas.cli import main
from disclosure_atlas.connectors.base import ParseContext
from disclosure_atlas.connectors.se_fi_pdmr import SwedenFIPDMR
from disclosure_atlas.events.bus import DeliveryError
from disclosure_atlas.events.contracts import EVENT_TYPES, FILING_PUBLISHED, SNAPSHOT_COMMITTED, stale_contracts
from disclosure_atlas.events.publisher import ChangeFeedPublisher, parse_stream_id
from disclosure_atlas.events.registry import SchemaRegistry
from disclosure_atlas.lake import Lake
from disclosure_atlas.models import Dataset


class FakeBus:
    def __init__(self, fail_on_flush: bool = False) -> None:
        self.pending: list[dict] = []
        self.delivered: list[dict] = []
        self.fail_on_flush = fail_on_flush

    def produce(self, topic, key, value, headers):
        self.pending.append({"topic": topic, "key": key, "value": json.loads(value), "headers": dict(headers)})

    def flush(self):
        if self.fail_on_flush:
            self.pending.clear()
            raise DeliveryError("broker unavailable")
        self.delivered += self.pending
        self.pending = []

    def on(self, topic):
        return [m for m in self.delivered if m["topic"] == topic]


def _load(lake: Lake, rows, fetched_at: datetime) -> None:
    connector = SwedenFIPDMR(http=object())
    doc = fi_doc(rows, fetched_at=fetched_at)
    records = list(connector.parse(doc, ParseContext(pseudonym_key=lake.pseudonym_key())))
    lake.write_silver(Dataset.INSIDER_TRANSACTIONS, connector.name, records)


T0 = datetime(2026, 10, 2, 22, 0, tzinfo=UTC)
BUY = fi_row()
SELL = fi_row(**{"Nature of transaction": "Disposal", "Volume": "10.0"})
OTHER = fi_row(**{"Issuer": "Other Example AB", "LEI-code": "", "Volume": "5.0"})


@pytest.fixture
def lake(tmp_path):
    lake = Lake(tmp_path / "lake")
    _load(lake, [BUY], T0)
    return lake


def test_starts_at_end_then_publishes_only_new_rows(lake):
    bus = FakeBus()
    publisher = ChangeFeedPublisher(lake, bus)
    first = publisher.publish()
    assert first.filings == 0 and not bus.on(FILING_PUBLISHED.topic)

    _load(lake, [BUY, SELL, OTHER], T0 + timedelta(minutes=5))  # BUY again (unchanged), SELL and OTHER new
    second = publisher.publish()
    filings = bus.on(FILING_PUBLISHED.topic)
    assert second.filings == 2 and len(filings) == 2
    by_issuer = {m["value"]["issuer_name"]: m for m in filings}
    assert by_issuer["Example Robotics AB (publ)"]["key"] == EXAMPLE_LEI.encode()
    other = by_issuer["Other Example AB"]
    assert other["key"] == other["value"]["id"].encode()  # no LEI: keyed by record id
    for message in filings:
        Transaction.model_validate(message["value"])  # same payload as the REST API and SSE stream
        h = message["headers"]
        assert h["ce_specversion"] == b"1.0" and h["ce_type"] == FILING_PUBLISHED.type.encode()
        assert h["ce_id"] == message["value"]["id"].encode()

    snapshots = bus.on(SNAPSHOT_COMMITTED.topic)
    assert [s["value"]["filings_published"] for s in snapshots] == [0, 2]
    assert snapshots[1]["value"]["previous_snapshot"] == snapshots[0]["value"]["snapshot"]
    assert snapshots[1]["value"]["checkpoint"] == second.checkpoint

    assert publisher.publish().filings == 0  # nothing new: idempotent run


def test_checkpoint_does_not_move_when_delivery_fails(lake):
    publisher = ChangeFeedPublisher(lake, FakeBus())
    publisher.publish()
    before = publisher.load_checkpoint().position
    _load(lake, [SELL], T0 + timedelta(minutes=5))
    failing = ChangeFeedPublisher(lake, FakeBus(fail_on_flush=True))
    with pytest.raises(DeliveryError):
        failing.publish()
    assert failing.load_checkpoint().position == before
    retry_bus = FakeBus()
    assert ChangeFeedPublisher(lake, retry_bus).publish().filings == 1  # re-published, not lost


def test_backfill_publishes_history_in_order(lake):
    _load(lake, [SELL], T0 + timedelta(minutes=5))
    bus = FakeBus()
    report = ChangeFeedPublisher(lake, bus).publish(backfill=True)
    assert report.filings == 2
    positions = [parse_stream_id(f"{m['value']['first_seen_at']}|{m['value']['id']}")
                 for m in bus.on(FILING_PUBLISHED.topic)]
    assert positions == sorted(positions)


def test_contracts_are_committed_and_current(tmp_path, capsys):
    assert stale_contracts("contracts/events") == []
    out = tmp_path / "events"
    assert main(["contracts", "--out", str(out), "--check"]) == 1
    assert main(["contracts", "--out", str(out)]) == 0
    assert main(["contracts", "--out", str(out), "--check"]) == 0
    schema = json.loads((out / "filing-published.v1.json").read_text())
    assert schema["x-kafka-topic"] == "atlas.filings.v1"
    assert set(schema["required"]) >= {"id", "first_seen_at", "issuer_name"}


def test_schema_registry_sets_backward_compatibility_and_registers():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, json.loads(request.content)))
        if request.method == "POST":
            return httpx.Response(200, json={"id": len(calls)})
        return httpx.Response(200, json={"compatibility": "BACKWARD"})

    registry = SchemaRegistry("http://registry:8081/", httpx.Client(transport=httpx.MockTransport(handler)))
    ids = registry.register_all()
    assert set(ids) == {e.subject for e in EVENT_TYPES}
    assert ("PUT", "/config/atlas.filings.v1-value", {"compatibility": "BACKWARD"}) in calls
    post = next(c for c in calls if c[0] == "POST")
    assert post[1] == "/subjects/atlas.filings.v1-value/versions" and post[2]["schemaType"] == "JSON"


def test_schema_registry_rejects_incompatible_change():
    registry = SchemaRegistry("http://registry", httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(409, json={"message": "incompatible"}))))
    with pytest.raises(RuntimeError, match="BACKWARD"):
        registry.register(FILING_PUBLISHED)


def test_publish_command_requires_bootstrap(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ATLAS_KAFKA_BOOTSTRAP", raising=False)
    assert main(["--lake", str(tmp_path / "lake"), "publish"]) == 2


def test_watch_once_publishes_after_ingest(tmp_path, monkeypatch, capsys):
    bus = FakeBus()
    monkeypatch.setattr("disclosure_atlas.events.publisher.from_env",
                        lambda lake, env: ChangeFeedPublisher(lake, bus))
    monkeypatch.setattr(SwedenFIPDMR, "fetch", lambda self, start, end: iter([fi_doc([BUY, SELL])]))
    assert main(["--lake", str(tmp_path / "lake"), "watch", "se_fi_pdmr", "--once"]) == 0
    assert len(bus.on(FILING_PUBLISHED.topic)) == 2  # empty lake at start: everything ingested is new
    assert "published 2 filings" in capsys.readouterr().out
