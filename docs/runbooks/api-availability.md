# Query API availability

**Alerts:** `AtlasApiAvailabilityBudgetBurn` (page, ticket), `AtlasQueryApiDown` (page).
**SLO:** Over 30 days, at least 99.9% of recorded `/v1` requests, excluding `/v1/stream`, return without
a 5xx. The budget is 0.1% of eligible requests. Owner: platform.
[SLO specification](../../deploy/observability/slos/query-api.yaml)

## What it means
`AtlasApiAvailabilityBudgetBurn` divides the 5xx count by all recorded requests on eligible routes,
including recorded 4xx responses in that total.

`AtlasQueryApiDown` looks for ten minutes without either query API request counts or source-ingestion
timestamps, with the condition held for five minutes. Check both HTTP serving and telemetry
delivery. [Symptom alert rules](../../deploy/observability/prometheus/rules/atlas-alerts.yaml)

## Confirm
1. Grafana *Query API: RED*: error ratio panel and *Responses by status*. Is it one route or all of them?
2. Run `curl -si "${API%/}/v1/health"` with `API` set to the API base URL. A successful response reports
   the current snapshot and the count of insider transactions it can read. Check the failing route
   separately, especially if it reads another table or uses a pinned snapshot.
3. If the health probe succeeds while request metrics are missing, follow the
   [telemetry pipeline runbook](telemetry-pipeline.md).
4. Loki: `{service_name="query-api"} | detected_level="ERROR"`, then open the trace from its `trace_id`.
5. Kubernetes: `kubectl -n atlas get pods -l app.kubernetes.io/name=query-api` and recent events
   (OOMKilled, failing readiness probes).

## Mitigate
- Bad deploy (errors began with a rollout): roll back in Argo CD (`argocd app rollback query-api`) or revert
  the image tag in Git; GitOps reconciles it.
- Lake unreadable (missing or corrupt Parquet after a failed ingest): the read model pins a version; point
  `ATLAS_LAKE` at the last good snapshot, or (DuckLake, P2) pin `as_of` to the last committed snapshot.
- Memory pressure: scale out (`kubectl scale` or raise the HPA minimum) and lower `limit` defaults if one
  query shape is responsible.
- Abuse: if one client causes load, lower `ATLAS_RATE_LIMIT_PER_MINUTE` or block it at the gateway (Envoy).

## Fix and follow up
Record the timeline, share of 5xx responses, impact on the request budget, cause, and actions taken.
Add a regression test for the failing route under `tests/`.
