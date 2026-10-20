"""Grafana dashboards as code: `python deploy/observability/grafana/dashboards.py` writes ./dashboards/*.json.

Plain Python, no dependencies, so CI can check that the committed JSON matches this file. Metric names follow
the OpenTelemetry instruments in src/disclosure_atlas/api/telemetry.py as exported by the Collector.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "dashboards"
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
TEMPO = {"type": "tempo", "uid": "tempo"}
API = 'job="query-api", http_route=~"/v1/.+", http_route!="/v1/stream"'


class Board:
    def __init__(self, uid: str, title: str, description: str, tags: list[str]) -> None:
        self.uid, self.title, self.description, self.tags = uid, title, description, tags
        self.panels: list[dict] = []
        self.x = self.y = self.row_height = 0

    def _place(self, width: int, height: int) -> dict:
        if self.x + width > 24:
            self.x, self.y = 0, self.y + self.row_height
            self.row_height = 0
        pos = {"x": self.x, "y": self.y, "w": width, "h": height}
        self.x += width
        self.row_height = max(self.row_height, height)
        return pos

    def row(self, title: str) -> Board:
        if self.x:
            self.x, self.y, self.row_height = 0, self.y + self.row_height, 0
        self.panels.append({"type": "row", "title": title, "collapsed": False, "panels": [],
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}})
        self.y += 1
        return self

    def panel(self, kind: str, title: str, targets: list[dict], *, width: int = 12, height: int = 8,
              unit: str = "short", description: str = "", datasource: dict = PROM, thresholds: list | None = None,
              options: dict | None = None, overrides: list | None = None) -> Board:
        steps = [{"color": "green", "value": None}] + [
            {"color": color, "value": value} for value, color in (thresholds or [])
        ]
        self.panels.append({
            "type": kind, "title": title, "description": description, "datasource": datasource,
            "gridPos": self._place(width, height),
            "targets": [{"refId": chr(65 + i), "datasource": datasource, **t} for i, t in enumerate(targets)],
            "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}},
                            "overrides": overrides or []},
            "options": options or {},
        })
        return self

    def json(self) -> dict:
        for i, p in enumerate(self.panels, start=1):
            p["id"] = i
        return {
            "uid": self.uid, "title": self.title, "description": self.description, "tags": ["atlas", *self.tags],
            "schemaVersion": 41, "version": 1, "editable": False, "timezone": "utc", "refresh": "30s",
            "time": {"from": "now-6h", "to": "now"}, "graphTooltip": 1,
            "links": [{"title": "Runbooks", "type": "link", "targetBlank": True,
                       "url": "https://github.com/Manuelma4/eu-disclosure-atlas/tree/main/docs/runbooks"}],
            "panels": self.panels,
        }


def q(expr: str, legend: str = "", **extra) -> dict:
    return {"expr": expr, "legendFormat": legend, **extra}


def api_red() -> Board:
    rate = f"sum(rate(http_server_request_duration_seconds_count{{{API}}}[$__rate_interval]))"
    errors = (f"(sum(rate(http_server_request_duration_seconds_count{{{API}, http_response_status_code=~\"5..\"}}"
              "[$__rate_interval])) or on() vector(0))")
    b = Board("atlas-api-red", "Query API: RED", "Rate, errors and duration of the public /v1 API, with "
              "its SLOs (99.9% availability, p95 under 200 ms).", ["api", "red"])
    b.row("Service level")
    b.panel("stat", "Availability (30d SLI)", [q('1 - slo:sli_error:ratio_rate30d{sloth_id="query-api-requests-'
                                                 'availability"}')], width=6, height=4, unit="percentunit",
            thresholds=[(0, "red"), (0.999, "green")])
    b.panel("stat", "Within 200 ms (30d SLI)", [q('1 - slo:sli_error:ratio_rate30d{sloth_id="query-api-requests-'
                                                  'latency"}')], width=6, height=4, unit="percentunit",
            thresholds=[(0, "red"), (0.95, "green")])
    b.panel("stat", "Error budget left (availability)",
            [q('slo:period_error_budget_remaining:ratio{sloth_id="query-api-requests-availability"}')],
            width=6, height=4, unit="percentunit", thresholds=[(0, "red"), (0.25, "yellow"), (0.5, "green")])
    b.panel("stat", "p95 latency (5m)", [q(
        f"histogram_quantile(0.95, sum by (le) (rate(http_server_request_duration_seconds_bucket{{{API}}}[5m])))")],
        width=6, height=4, unit="s", thresholds=[(0.2, "red")])
    b.row("Rate, errors, duration")
    b.panel("timeseries", "Requests per second by route", [q(
        f"sum by (http_route) (rate(http_server_request_duration_seconds_count{{{API}}}[$__rate_interval]))",
        "{{http_route}}")], unit="reqps")
    b.panel("timeseries", "Error ratio (5xx)", [q(f"{errors} / {rate}", "5xx ratio")], unit="percentunit",
            thresholds=[(0.001, "red")])
    b.panel("timeseries", "Latency p50 / p95 / p99", [
        q(f"histogram_quantile({p}, sum by (le) (rate(http_server_request_duration_seconds_bucket{{{API}}}"
          f"[$__rate_interval])))", f"p{int(p * 100)}", exemplar=True)
        for p in (0.5, 0.95, 0.99)
    ], unit="s", thresholds=[(0.2, "red")], description="Exemplars link slow points to their traces in Tempo.")
    b.panel("timeseries", "p95 latency by route", [q(
        f"histogram_quantile(0.95, sum by (le, http_route) (rate(http_server_request_duration_seconds_bucket"
        f"{{{API}}}[$__rate_interval])))", "{{http_route}}")], unit="s", thresholds=[(0.2, "red")])
    b.panel("timeseries", "Responses by status", [q(
        'sum by (http_response_status_code) (rate(http_server_request_duration_seconds_count{job="query-api"}'
        '[$__rate_interval]))', "{{http_response_status_code}}")], unit="reqps",
        description="429 responses come from the per-IP rate limit; 304 from ETag revalidation.")
    b.row("DuckDB")
    b.panel("timeseries", "Query p95 duration", [q(
        'histogram_quantile(0.95, sum by (le) (rate(atlas_db_query_duration_seconds_bucket{job="query-api"}'
        '[$__rate_interval])))', "p95")], unit="s")
    b.panel("timeseries", "Queries per second", [q(
        'sum by (error_type) (rate(atlas_db_query_duration_seconds_count{job="query-api"}[$__rate_interval]))',
        "{{error_type}}")], unit="ops")
    b.row("Logs and traces")
    b.panel("logs", "API logs", [{"expr": '{service_name="query-api"}'}], width=24, datasource=LOKI)
    b.panel("table", "Slow or failed traces", [{"queryType": "traceql", "limit": 20,
                                                "query": '{resource.service.name="query-api" && '
                                                         '(duration > 200ms || status = error)}'}],
            width=24, datasource=TEMPO)
    return b


def ingestion() -> Board:
    b = Board("atlas-ingestion-freshness", "Ingestion: freshness per source", "Filings visible within 15 minutes "
              "of publication (SLO 95%), last ingestion per source, late and backfilled filings.",
              ["ingestion", "freshness"])
    b.row("Freshness SLO")
    b.panel("bargauge", "On time, 30 days, by source", [q(
        '1 - slo:sli_error:ratio_rate30d{sloth_id="ingestion-filing-freshness"}', "{{source}}")],
        width=12, unit="percentunit", thresholds=[(0, "red"), (0.95, "green")],
        options={"orientation": "horizontal", "displayMode": "basic"})
    b.panel("table", "Minutes since last ingestion", [q(
        "(time() - max by (source) (atlas_source_last_ingested_seconds)) / 60", "{{source}}",
        instant=True, format="table")], width=12, unit="m", thresholds=[(60, "yellow"), (26 * 60, "red")])
    b.row("Filings")
    b.panel("timeseries", "Filings ingested per hour by timeliness", [q(
        "sum by (source, timeliness) (increase(atlas_source_filings_total[1h]))", "{{source}} {{timeliness}}")],
        width=12)
    b.panel("timeseries", "Error budget burn rate (1h)", [q(
        'slo:sli_error:ratio_rate1h{sloth_id="ingestion-filing-freshness"} / on (source) group_left '
        'slo:error_budget:ratio{sloth_id="ingestion-filing-freshness"}', "{{source}}")],
        width=12, thresholds=[(1, "yellow"), (14.4, "red")],
        description="1 burns the budget exactly over 30 days; 14.4 pages.")
    b.panel("timeseries", "Lag between latest publication and latest ingestion", [q(
        "max by (source) (atlas_source_last_ingested_seconds) - max by (source) (atlas_source_last_published_seconds)",
        "{{source}}")], width=24, unit="s")
    return b


def realtime() -> Board:
    b = Board("atlas-realtime", "Real time: SSE clients and event delay", "Live-stream connections and the delay "
              "from ingestion (or Kafka) to delivery; SLO: 99% within 5 seconds.", ["realtime", "sse"])
    b.panel("stat", "Connected SSE clients", [q("sum(atlas_stream_clients)")], width=6, height=5)
    b.panel("stat", "Events within 5 s (30d SLI)", [q(
        '1 - slo:sli_error:ratio_rate30d{sloth_id="realtime-event-delay"}')], width=6, height=5,
        unit="percentunit", thresholds=[(0, "red"), (0.99, "green")])
    b.panel("stat", "Events per minute", [q("sum(rate(atlas_stream_events_total[5m])) * 60")], width=6, height=5)
    b.panel("stat", "p95 delivery delay", [q(
        "histogram_quantile(0.95, sum by (le) (rate(atlas_stream_event_delay_seconds_bucket[15m])))")],
        width=6, height=5, unit="s", thresholds=[(5, "red")])
    b.panel("timeseries", "SSE clients by service", [q("sum by (job) (atlas_stream_clients)", "{{job}}")])
    b.panel("timeseries", "Delivery delay p50 / p95", [
        q(f"histogram_quantile({p}, sum by (le, job) (rate(atlas_stream_event_delay_seconds_bucket"
          f"[$__rate_interval])))", f"{{{{job}}}} p{int(p * 100)}") for p in (0.5, 0.95)
    ], unit="s", thresholds=[(5, "red")])
    return b


def kafka() -> Board:
    lag = ('sum by (group) (redpanda_kafka_consumer_group_lag_sum) or sum by (group) (label_replace('
           'kafka_consumergroup_lag, "group", "$1", "consumergroup", "(.*)"))')
    b = Board("atlas-kafka-lag", "Kafka: consumer lag (placeholder)", "Consumer-group lag for the event backbone "
              "(decision 0011). Fills in once P3's consumers run: Redpanda locally, kafka-exporter on Strimzi.",
              ["kafka", "events"])
    b.panel("timeseries", "Lag by consumer group (records)", [q(lag, "{{group}}")], width=24,
            thresholds=[(1000, "red")])
    b.panel("timeseries", "Produced records per second by topic", [q(
        'sum by (redpanda_topic) (rate(redpanda_kafka_records_produced_total[$__rate_interval]))',
        "{{redpanda_topic}}")], width=24)
    return b


def slos() -> Board:
    b = Board("atlas-slos", "SLO overview", "Every SLO, its 30-day SLI, remaining error budget and firing "
              "burn-rate alerts. Specs: deploy/observability/slos/.", ["slo"])
    b.panel("table", "SLOs", [
        q("slo:objective:ratio", "", instant=True, format="table"),
        q("1 - slo:sli_error:ratio_rate30d", "", instant=True, format="table"),
        q("slo:period_error_budget_remaining:ratio", "", instant=True, format="table"),
    ], width=24, height=10, unit="percentunit",
        options={"showHeader": True}, description="Objective, SLI over 30 days and error budget remaining.")
    b.panel("timeseries", "Remaining error budget", [q(
        "slo:period_error_budget_remaining:ratio", "{{sloth_service}}/{{sloth_slo}} {{source}}")],
        width=24, unit="percentunit", thresholds=[(0, "red")])
    b.panel("table", "Firing alerts", [q('ALERTS{alertstate="firing"}', "", instant=True, format="table")],
            width=24)
    return b


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for build in (api_red, ingestion, realtime, kafka, slos):
        board = build()
        name = board.uid.removeprefix("atlas-")
        (OUT / f"{name}.json").write_text(json.dumps(board.json(), indent=2) + "\n")


if __name__ == "__main__":
    main()
