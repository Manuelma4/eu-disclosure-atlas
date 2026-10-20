# 0014: Observability, SLOs and MLOps

Status: accepted (2026-10-20)

## Context
A public data product fails in quiet ways: a regulator changes a format, a parser drifts, a consumer lags,
a model degrades. Each needs a signal and an owner.

## Decision
- **Telemetry:** OpenTelemetry SDKs in Python, Go and Node.js, exported to an OpenTelemetry Collector, then to
  Prometheus (metrics), Loki (logs) and Tempo (traces), with dashboards as code in Grafana.
- **SLOs as code** (Pyrra or Sloth), with multi-window burn-rate alerts:
  - filings visible within 15 minutes of publication for 95% of business hours, per source;
  - `/v1` reads available 99.9% of the time, p95 latency under 200 ms;
  - live events delivered within 5 seconds of publication to Kafka.
- **Data observability:** Dagster asset checks (freshness, volume, checksums, parser coverage) and OpenLineage
  events for lineage.
- **LLM observability:** Langfuse traces for every model call (prompt version, tokens, latency, cost) with
  evaluation scores attached; CI gates on the golden sets.
- **MLOps:** a model that scores the informativeness of insider trades, trained on gold features, with
  experiments and the model registry in MLflow, walk-forward validation (no look-ahead), batch scoring as a
  Dagster asset, a model card, and drift monitoring. It is research output, never investment advice.
- **Resilience:** k6 load tests with budgets in CI; chaos experiments (Chaos Mesh) in the preview cluster;
  runbooks for each alert.

## Consequences
- Every alert links to a runbook and an SLO, so on-call decisions are explicit.
- Telemetry adds overhead; sampling keeps it bounded (tail sampling in the Collector).
