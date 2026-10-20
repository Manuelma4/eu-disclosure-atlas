# Query API latency

**Alert:** `AtlasApiLatencyBudgetBurn` (page, ticket).
**SLO:** 95% of `/v1` reads complete within 200 ms over 30 days (p95 under 200 ms). `/v1/stream` is excluded.
Owner: platform.

## Confirm
1. Grafana *Query API: RED*: *p95 latency by route* shows which route is slow; click an exemplar dot to open
   a slow trace.
2. In the trace, compare the request span with its `SELECT gold` (DuckDB) child spans. If DuckDB dominates,
   the query or the data shape changed; if not, look at CPU throttling or the event loop (SSE load).
3. *DuckDB* row: query p95. A step change right after an ingest often means a new silver file layout.

## Mitigate
- Cold cache after a new snapshot: responses are cacheable by snapshot (ETag); check the CDN hit ratio and
  the `Cache-Control` headers.
- One expensive route (e.g. issuer detail for a very active issuer): scale out, then fix the query.
- Too many small Parquet files: compact silver (P2 DuckLake compaction) and restart pods to reopen.
- CPU throttling: raise CPU requests or replicas (HPA on p95 latency or CPU).

## Fix
Profile the query with `disclosure-atlas query "EXPLAIN ANALYZE ..."`, add a k6 scenario reproducing the slow
path (`loadtests/`), and keep the budget green in CI.
