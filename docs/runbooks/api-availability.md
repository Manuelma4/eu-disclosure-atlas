# Query API availability

**Alerts:** `AtlasApiAvailabilityBudgetBurn` (page, ticket), `AtlasQueryApiDown` (page).
**SLO:** 99.9% of `/v1` reads succeed without a 5xx over 30 days (43 minutes of full outage). Owner: platform.

## What it means
The query API is returning server errors, or (for `AtlasQueryApiDown`) exports no telemetry at all: it is
down, crash-looping, or cannot reach the Collector. 4xx responses (bad input, 404, 429) never burn this budget.

## Confirm
1. Grafana *Query API: RED*: error ratio panel and *Responses by status*. Is it one route or all of them?
2. `curl -si $API/v1/health`. A 5xx with a DuckDB error usually means the lake is unreadable.
3. Loki: `{service_name="query-api"} | detected_level="ERROR"`, then open the trace from its `trace_id`.
4. Kubernetes: `kubectl -n atlas get pods -l app.kubernetes.io/name=query-api` and recent events
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
Write a short incident note (timeline, impact in budget minutes, cause, actions). Add a regression test for
the failing route under `tests/`.
