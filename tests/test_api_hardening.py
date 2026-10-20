"""Security headers, rate limiting and OpenTelemetry instrumentation of the query API (P7)."""

import pytest
from fastapi.testclient import TestClient
from test_api import lake  # noqa: F401 - shared synthetic lake fixture

from disclosure_atlas.api import create_app, telemetry
from disclosure_atlas.api.security import RateLimitMiddleware


def test_security_headers_on_api_and_docs(lake):  # noqa: F811
    client = TestClient(create_app(str(lake.root)))
    api = client.get("/v1/health")
    assert api.headers["x-content-type-options"] == "nosniff"
    assert api.headers["x-frame-options"] == "DENY"
    assert api.headers["content-security-policy"].startswith("default-src 'none'")
    assert "strict-transport-security" in api.headers
    docs = client.get("/docs")
    assert "https://cdn.jsdelivr.net" in docs.headers["content-security-policy"]


def test_rate_limit_returns_problem_with_retry_after(lake):  # noqa: F811
    client = TestClient(create_app(str(lake.root), rate_limit=(60, 2)))
    assert [client.get("/v1/sources").status_code for _ in range(2)] == [200, 200]
    limited = client.get("/v1/sources")
    assert limited.status_code == 429
    assert limited.headers["content-type"] == "application/problem+json"
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.json()["status"] == 429
    assert limited.headers["x-content-type-options"] == "nosniff"
    assert client.get("/v1/health").status_code == 200  # probes are never limited


def test_rate_limit_can_be_disabled(lake):  # noqa: F811
    client = TestClient(create_app(str(lake.root), rate_limit=(0, 0)))
    assert all(client.get("/v1/sources").status_code == 200 for _ in range(5))


def test_token_bucket_refills_and_prunes():
    limiter = RateLimitMiddleware(app=None, per_minute=60, burst=2)
    assert limiter._take("a", 0.0) == 0 and limiter._take("a", 0.0) == 0
    assert limiter._take("a", 0.0) == pytest.approx(1.0)
    assert limiter._take("a", 1.0) == 0  # one token per second
    limiter._prune(10.0)
    assert limiter.buckets == {}


def test_telemetry_is_off_without_endpoint(monkeypatch, lake):  # noqa: F811
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    create_app(str(lake.root))
    assert not telemetry.enabled()


@pytest.fixture
def otel(lake):  # noqa: F811
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    spans, reader = InMemorySpanExporter(), InMemoryMetricReader()
    # A 1 s poll keeps the stream open while the test reads the gauge (0.01 s let it close first: flaky).
    app = create_app(str(lake.root), stream_poll_seconds=1.0)
    stats = lambda: [{"source": "se_fi_pdmr", "on_time": 2, "late": 1, "backfill": 0,  # noqa: E731
                      "last_ingested": 1.0, "last_published": 2.0}]
    assert telemetry.configure(app, stats, span_exporter=spans, metric_reader=reader)
    yield TestClient(app), spans, reader
    telemetry._state = telemetry._State()


def _metrics(reader) -> dict:
    data = reader.get_metrics_data()
    return {m.name: m for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}


def test_requests_produce_server_and_duckdb_spans_and_red_metrics(otel):
    client, spans, reader = otel
    assert client.get("/v1/insider-transactions", params={"limit": 1}).status_code == 200
    names = [s.name for s in spans.get_finished_spans()]
    assert "GET /v1/insider-transactions" in names
    query = next(s for s in spans.get_finished_spans() if s.attributes.get("db.system.name") == "duckdb")
    assert query.attributes["db.operation.name"] == "SELECT"
    assert query.parent is not None  # nested under the request span
    metrics = _metrics(reader)
    duration = metrics["http.server.request.duration"].data.data_points[0]
    assert 0.2 in duration.explicit_bounds  # the latency SLO threshold is a bucket boundary
    assert "atlas.db.query.duration" in metrics
    filings = {(p.attributes["timeliness"]): p.value for p in metrics["atlas.source.filings"].data.data_points}
    assert filings == {"on_time": 2, "late": 1, "backfill": 0}


def test_source_stats_sql_runs_on_the_gold_views(lake):  # noqa: F811
    rows = lake.connect().sql(telemetry.SOURCE_STATS_SQL).fetchall()
    assert rows and rows[0][0] == "se_fi_pdmr"
    assert sum(rows[0][1:4]) == 3


def test_stream_counts_clients_and_event_delay(otel):
    client, _, reader = otel
    with client.stream("GET", "/v1/stream", params={"since": "2000-01-01T00:00:00+00:00|"}) as response:
        lines = response.iter_lines()  # keep a reference: a dropped iterator is closed at once, ending the stream
        for line in lines:
            if line.startswith("event: filing"):
                break
        assert _metrics(reader)["atlas.stream.clients"].data.data_points[0].value == 1
    metrics = _metrics(reader)
    assert metrics["atlas.stream.events"].data.data_points[0].value >= 1
    assert 5.0 in metrics["atlas.stream.event.delay"].data.data_points[0].explicit_bounds
