# Query API latency

**Alert:** `AtlasApiLatencyBudgetBurn` (page, ticket).
**SLO:** Over 30 days, 95% of eligible recorded `/v1` requests complete within
200 ms, excluding `/v1/stream`. Owner: platform.
[Latency SLO specification](../../deploy/observability/slos/query-api.yaml)

## Confirm
1. Grafana *Query API: RED*: *p95 latency by route* shows which route is slow; click an exemplar dot to open
   a slow trace.
2. In the trace, compare the request span with its `SELECT gold` (DuckDB) child spans. If DuckDB dominates,
   the query or the data shape changed; if not, look at CPU throttling or the event loop (SSE load).
3. *DuckDB* row: query p95. A step change right after an ingest often means a new silver file layout.

## Mitigate
- Cold cache after a new snapshot: compare warm and cold requests on the same route. For routes that
  return an ETag, record `X-Atlas-Snapshot`, `Cache-Control` and the response status. Keep pinned and
  live requests separate in the comparison.
- One expensive route (e.g. issuer detail for a very active issuer): scale out, then fix the query.
- Too many small Parquet files: compact silver (P2 DuckLake compaction) and restart pods to reopen.
- CPU throttling: raise CPU requests or replicas (HPA on p95 latency or CPU).

## Fix
On a local synthetic lake, profile SQL against the gold views.

```sh
disclosure-atlas --lake /tmp/atlas-lake query 'EXPLAIN ANALYZE SELECT count(*) FROM insider_transactions'
```

Seed that lake with the synthetic fixture in the load-test guide below. Use the
relevant read-only SQL when profiling a different query. Use the API trace to
measure request handling and cache effects.
If a request-level regression needs a k6 case, see the [load-test guide](../../loadtests/README.md)
for the separate synthetic smoke check. The smoke run is scheduled and can also
be dispatched manually.
