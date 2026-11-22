# Load tests (k6)

Scenarios for the public API with performance budgets (decision 0014). One script, three profiles:

| Profile | Shape | Where |
|---|---|---|
| `smoke` | ~5 REST users and 3 live-stream clients for 50 s | weekly schedule, manual workflow dispatch, or local k6 |
| `load` | up to 50 REST users over 5 min 30 s, plus 100 stream clients for 5 min | manual workflow dispatch or local k6 |
| `stress` | up to 200 REST users and 500 stream clients for 8 minutes | direct k6 execution against an isolated synthetic test environment |

The [load-test workflow](../.github/workflows/loadtest.yml) seeds a local
synthetic lake with 20,000 transactions. The weekly run uses the smoke profile.
Manual dispatch offers `smoke` and `load`.

Scenarios (weights of REST users): **overview** (30%: overview, weekly flow, sources, then an `If-None-Match`
revalidation), **filings pagination** (30%: five pages by cursor, then a filtered query), **issuer pages**
(40%: a random issuer's detail and filings), **SSE connections** (stream clients that hold `/v1/stream` open
for 8 seconds and count the `filing` events they receive).

## Budgets (thresholds; the run fails when one is missed)
- REST `p(95) < 200 ms` and overview revalidations (`304`) `p(95) < 50 ms`.
  The script uses live URLs and sends `If-None-Match` when the overview response includes an ETag;
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
