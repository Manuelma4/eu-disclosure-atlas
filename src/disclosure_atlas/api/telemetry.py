"""OpenTelemetry for the query API: traces, RED metrics, DuckDB query spans and logs over OTLP.

Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set and the `otel` extra is installed; every hook below is then a
cheap no-op. The standard OTEL_* variables (service name, headers, sampler, resource attributes) apply.
Metric names are a contract with the SLOs in deploy/observability/slos/ (see docs/decisions/0014).
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

try:
    from opentelemetry import trace
    from opentelemetry.metrics import CallbackOptions, Observation
except ImportError:  # the `otel` extra is not installed
    trace = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

# Bucket boundaries (seconds) that contain the SLO thresholds: 200 ms for /v1 latency, 5 s for live events.
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.5, 5.0, 10.0)
DELAY_BUCKETS = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 300.0, 900.0)
# A filing is on time when it is visible within 15 minutes of publication; a lag over a day is a backfill
# (history loaded later), which says nothing about live freshness and is counted apart.
ON_TIME_SECONDS = 15 * 60
BACKFILL_SECONDS = 24 * 3600
SOURCE_STATS_SQL = f"""
SELECT source,
    count(*) FILTER (WHERE lag <= {ON_TIME_SECONDS}) AS on_time,
    count(*) FILTER (WHERE lag > {ON_TIME_SECONDS} AND lag <= {BACKFILL_SECONDS}) AS late,
    count(*) FILTER (WHERE lag > {BACKFILL_SECONDS}) AS backfill,
    epoch(max(first_seen_at)) AS last_ingested, epoch(max(published_at)) AS last_published
FROM (
    SELECT source, published_at, first_seen_at,
        greatest(epoch(first_seen_at) - epoch(published_at), 0) AS lag
    FROM insider_transactions WHERE published_at IS NOT NULL AND first_seen_at IS NOT NULL
) GROUP BY source
"""


class _State:
    """Instruments of the active configuration; None until `configure` succeeds."""

    tracer: Any = None
    query_duration: Any = None
    stream_clients: Any = None
    stream_events: Any = None
    stream_delay: Any = None


_state = _State()


def enabled() -> bool:
    return _state.tracer is not None


def configure(
    app: Any,
    source_stats: Callable[[], list[dict[str, Any]]],
    *,
    span_exporter: Any = None,
    metric_reader: Any = None,
) -> bool:
    """Instrument `app` when OTLP is configured (or exporters are injected, as in tests). Returns True if on."""
    injected = span_exporter is not None or metric_reader is not None
    if trace is None or not (injected or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")):
        return False
    # Stable HTTP semantic conventions: `http.server.request.duration` in seconds with `http.route`.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")
    try:
        from opentelemetry import metrics
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except ImportError:
        log.warning("OTEL_EXPORTER_OTLP_ENDPOINT is set but the `otel` extra is not installed")
        return False

    from .. import __version__

    resource = Resource.create({
        "service.name": os.environ.get("OTEL_SERVICE_NAME", "query-api"),
        "service.version": __version__,
        "deployment.environment.name": os.environ.get("ATLAS_ENVIRONMENT", "local"),
    })
    tracer_provider = TracerProvider(resource=resource)
    if span_exporter is not None:
        tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if metric_reader is None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=15_000)
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
        views=[
            View(instrument_name="http.server.request.duration",
                 aggregation=ExplicitBucketHistogramAggregation(LATENCY_BUCKETS)),
            View(instrument_name="atlas.db.query.duration",
                 aggregation=ExplicitBucketHistogramAggregation(LATENCY_BUCKETS)),
            View(instrument_name="atlas.stream.event.delay",
                 aggregation=ExplicitBucketHistogramAggregation(DELAY_BUCKETS)),
        ],
    )
    if not injected:
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
        _configure_logs(resource)

    meter = meter_provider.get_meter("disclosure_atlas.api", __version__)
    _state.tracer = tracer_provider.get_tracer("disclosure_atlas.api", __version__)
    _state.query_duration = meter.create_histogram(
        "atlas.db.query.duration", unit="s", description="DuckDB query duration over the gold views")
    _state.stream_clients = meter.create_up_down_counter(
        "atlas.stream.clients", unit="{client}", description="Open server-sent event connections")
    _state.stream_events = meter.create_counter(
        "atlas.stream.events", unit="{event}", description="Filing events sent on the live stream")
    _state.stream_delay = meter.create_histogram(
        "atlas.stream.event.delay", unit="s", description="Time from first ingestion to delivery on the stream")
    _register_source_metrics(meter, source_stats)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider, meter_provider=meter_provider)
    log.info("OpenTelemetry enabled for service %s", resource.attributes["service.name"])
    return True


def _configure_logs(resource: Any) -> None:
    """Ship standard-library logs to the Collector (then Loki), correlated with the active trace."""
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(provider)
    logging.getLogger().addHandler(LoggingHandler(level=logging.INFO, logger_provider=provider))
    logging.getLogger().setLevel(min(logging.getLogger().level or logging.INFO, logging.INFO))


def _register_source_metrics(meter: Any, source_stats: Callable[[], list[dict[str, Any]]]) -> None:
    """Freshness per source, computed from gold (the system of record) and cached for 30 seconds."""
    cache: dict[str, Any] = {"at": 0.0, "rows": []}

    def rows() -> list[dict[str, Any]]:
        if time.monotonic() - cache["at"] > 30:
            try:
                cache["rows"] = source_stats()
            except Exception:  # noqa: BLE001 - an empty or broken lake must not break metric export
                log.exception("Could not compute source freshness")
                cache["rows"] = []
            cache["at"] = time.monotonic()
        return cache["rows"]

    def filings(_: CallbackOptions) -> Iterator[Observation]:
        for row in rows():
            for timeliness in ("on_time", "late", "backfill"):
                yield Observation(row[timeliness], {"source": row["source"], "timeliness": timeliness})

    def timestamps(column: str) -> Callable[[CallbackOptions], Iterator[Observation]]:
        def observe(_: CallbackOptions) -> Iterator[Observation]:
            for row in rows():
                if row[column] is not None:
                    yield Observation(float(row[column]), {"source": row["source"]})
        return observe

    meter.create_observable_counter(
        "atlas.source.filings", [filings], unit="{filing}",
        description="Filings in gold by source and timeliness (on_time: visible within 15 minutes)")
    meter.create_observable_gauge(
        "atlas.source.last_ingested", [timestamps("last_ingested")], unit="s",
        description="Unix time of the most recent ingestion per source")
    meter.create_observable_gauge(
        "atlas.source.last_published", [timestamps("last_published")], unit="s",
        description="Unix time of the most recent publication per source")


_WHITESPACE = re.compile(r"\s+")


@contextmanager
def query_span(sql: str) -> Iterator[None]:
    """Trace one DuckDB query and record its duration. Parameters are never recorded."""
    if _state.tracer is None:
        yield
        return
    text = _WHITESPACE.sub(" ", sql).strip()
    operation = text.split(" ", 1)[0].upper()
    attributes = {"db.system.name": "duckdb", "db.operation.name": operation}
    start = time.perf_counter()
    with _state.tracer.start_as_current_span(
        f"{operation} gold", kind=trace.SpanKind.CLIENT, attributes={**attributes, "db.query.text": text[:2000]}
    ) as span:
        try:
            yield
        except Exception as exc:
            span.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
            attributes["error.type"] = type(exc).__name__
            raise
        finally:
            _state.query_duration.record(time.perf_counter() - start, attributes)


def stream_opened() -> None:
    if _state.stream_clients is not None:
        _state.stream_clients.add(1)


def stream_closed() -> None:
    if _state.stream_clients is not None:
        _state.stream_clients.add(-1)


def stream_event_sent(first_seen_at: datetime | None) -> None:
    if _state.stream_events is None:
        return
    _state.stream_events.add(1)
    if first_seen_at is not None:
        seen = first_seen_at if first_seen_at.tzinfo else first_seen_at.replace(tzinfo=UTC)
        _state.stream_delay.record(max((datetime.now(UTC) - seen).total_seconds(), 0.0))
