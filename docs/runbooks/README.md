# Runbooks

Every alert carries a `runbook_url` that points here (decision 0014). A runbook says what the alert means,
how to confirm it, how to mitigate first and fix second, and who owns it. SLO specs live in
[`deploy/observability/slos/`](../../deploy/observability/slos/); the alert rules Sloth generates from them,
plus the hand-written symptom alerts, live in
[`deploy/observability/prometheus/rules/`](../../deploy/observability/prometheus/rules/).

| Alert | Severity | SLO / signal | Runbook | Owner |
|---|---|---|---|---|
| `AtlasApiAvailabilityBudgetBurn` | page / ticket | `/v1` availability 99.9% | [api-availability.md](api-availability.md) | platform |
| `AtlasQueryApiDown` | page | no telemetry from the API | [api-availability.md](api-availability.md) | platform |
| `AtlasApiLatencyBudgetBurn` | page / ticket | `/v1` p95 under 200 ms | [api-latency.md](api-latency.md) | platform |
| `AtlasFilingFreshnessBudgetBurn` | page / ticket | filings visible within 15 min, 95%, per source | [filing-freshness.md](filing-freshness.md) | data |
| `AtlasSourceStale` | ticket | no ingestion for a business day | [source-stale.md](source-stale.md) | data |
| `AtlasLiveEventDelayBudgetBurn` | page / ticket | live events within 5 s, 99% | [live-event-delay.md](live-event-delay.md) | platform |
| `AtlasKafkaConsumerLagHigh` | ticket | consumer lag over 1,000 records | [kafka-consumer-lag.md](kafka-consumer-lag.md) | platform |
| `AtlasTelemetryPipelineDown` | ticket | Collector not scraped | [telemetry-pipeline.md](telemetry-pipeline.md) | platform |

## Burn-rate alerts in one paragraph
Sloth generates multi-window, multi-burn-rate alerts (the Google SRE workbook method) over a 30-day window.
`severity=page` fires when the budget burns 14.4 times too fast over 1 hour (confirmed over 5 minutes) or 6
times over 6 hours (confirmed over 30 minutes): at that pace a 30-day budget is gone in about 2 or 5 days.
`severity=ticket` fires at 3 times over 1 day or once over 3 days. A page means act now; a ticket means fix
this week. Silence an alert only with a linked issue.

## Common tools
- Grafana (local `http://localhost:3030`): dashboards *Query API: RED*, *Ingestion: freshness per source*,
  *Real time*, *Kafka*, *SLO overview*. Exemplar dots on latency panels open the matching trace in Tempo.
- Explore → Tempo: TraceQL `{resource.service.name="query-api" && duration > 200ms}`.
- Explore → Loki: `{service_name="query-api"} | detected_level="ERROR"`; click a `trace_id` to jump to the trace.
- Health: `curl -s $API/v1/health` returns the snapshot version and record count.
