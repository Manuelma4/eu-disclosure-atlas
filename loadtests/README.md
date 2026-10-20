# Load tests (k6)

Scenarios for the public API with performance budgets (decision 0014). One script, three profiles:

| Profile | Shape | Where |
|---|---|---|
| `smoke` | ~5 REST users and 3 live-stream clients for 50 s | every PR touching the API (`.github/workflows/loadtest.yml`) |
| `load` | 50 REST users and 100 stream clients for 5 minutes | on demand (workflow dispatch) or locally |
| `stress` | 200 REST users and 500 stream clients for 8 minutes | on demand, against a preview environment |

Scenarios (weights of REST users): **overview** (30%: overview, weekly flow, sources, then an `If-None-Match`
revalidation), **filings pagination** (30%: five pages by cursor, then a filtered query), **issuer pages**
(40%: a random issuer's detail and filings), **SSE connections** (stream clients that hold `/v1/stream` open
for 8 seconds and count the `filing` events they receive).

## Budgets (thresholds; the run fails when one is missed)
- REST `p(95) < 200 ms` with a warm server (snapshot-pinned responses are cached), revalidations
  (`304`) `p(95) < 50 ms`;
- REST and revalidation error rate under 1%, checks above 99%;
- more than 99% of stream connections open and start with the `retry:` preamble.

The k6 log prints one `Request Failed ... request timeout` warning per stream connection: that is the
client-side hold ending (error 1050), counted in `sse_connections_ok`, not a REST failure.

## Run locally
```bash
python loadtests/seed_lake.py /tmp/atlas-lake --transactions 20000
```
```bash
ATLAS_RATE_LIMIT_PER_MINUTE=0 disclosure-atlas --lake /tmp/atlas-lake serve --port 8000
```
```bash
k6 run -e BASE_URL=http://localhost:8000 -e PROFILE=smoke loadtests/atlas.js
```
Without a local k6, use Docker (`host.docker.internal` reaches the host on macOS and Windows):
```bash
docker run --rm -v "$PWD/loadtests":/s -e BASE_URL=http://host.docker.internal:8000 grafana/k6:1.1.0 run /s/atlas.js
```
The per-IP rate limit is disabled for load tests because every virtual user shares one address. With the
observability stack running and `OTEL_EXPORTER_OTLP_ENDPOINT` set, watch the run in Grafana's *Query API: RED*.

The seeded lake is entirely synthetic (generated issuers, identifiers and pseudonyms).

## Reference results
Smoke profile, 20,000 synthetic filings, on DuckLake (P2):
- laptop (Apple silicon), gold views evaluated per query: REST p95 33 ms; on a GitHub-hosted runner p95 235 ms
  (median 66 ms), which missed the budget;
- with the read model's per-snapshot gold cache (`src/disclosure_atlas/api/readmodel.py`): REST p95 14 ms,
  median 4.4 ms, revalidation p95 2 ms, 0% errors on the laptop.
