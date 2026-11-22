# Telemetry pipeline down

**Alert:** `AtlasTelemetryPipelineDown` (ticket). Owner: platform.
**Signal:** Prometheus reports `up{job="otel-collector"} == 0` for five minutes.
The local job scrapes application metrics at `otel-collector:8889`. A failed
scrape interrupts fresh samples from that target. Check the API separately to
determine whether requests still succeed. [Alert rule](../../deploy/observability/prometheus/rules/atlas-alerts.yaml) ·
[Local scrape configuration](../../deploy/observability/prometheus/prometheus.yml)

## Confirm in the local stack

1. Open the Prometheus Targets page at `http://localhost:9090/targets`. Inspect
   the scrape URL and last error for `otel-collector`. `otel-collector-self` is
   a separate job for Collector process metrics on port 8888.
2. From the repository root, inspect the Collector container and its recent logs.

   ```bash
   docker compose -f infra/compose.yaml -f deploy/observability/compose.yaml ps otel-collector
   docker compose -f infra/compose.yaml -f deploy/observability/compose.yaml logs --tail 100 otel-collector
   ```

3. Port 13133 is the Collector's health extension. In this Compose configuration,
   both that port and the application-metrics port are internal. Use the target
   status from Prometheus to verify scraping.
4. Once the scrape recovers, check that `query-api` request metrics are present
   after traffic to the local API. A successful scrape alone establishes that
   Prometheus can read the exporter. If application series are still missing,
   check the API's OTLP endpoint and instrumentation.

See [the local observability guide](../../deploy/observability/README.md) for the
Compose stack and the application metrics contract.
