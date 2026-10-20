# Telemetry pipeline down

**Alert:** `AtlasTelemetryPipelineDown` (ticket). Owner: platform.
**Signal:** Prometheus cannot scrape the OpenTelemetry Collector for 5 minutes. While this lasts, SLO alerts
see no traffic and cannot fire: treat it as urgent during business hours.

## Confirm and mitigate
1. `docker compose ... ps otel-collector` locally, or `kubectl -n observability get pods` in a cluster.
2. Collector logs: configuration errors after a change, or memory_limiter refusing data (raise the limit or
   scale the Collector tier).
3. Health endpoint: `curl -s collector:13133/`.
4. Applications keep serving: OTLP export failures are dropped after retries and never block requests.
