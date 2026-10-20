# realtime-gateway (Go)

Streams new filings from Kafka (`atlas.filings.v1`) to browsers as server-sent events on `GET /v1/stream`,
with the same frames as the Python API (`retry: 5000`, `id: <first_seen_at>|<record id>`, `event: filing`,
`data: <Transaction JSON>`, `: ping` every 15 s), so the website can switch between them with one variable.

- **Consumption:** franz-go, one consumer per replica without a group: each replica reads every partition,
  starting `ATLAS_GATEWAY_BACKFILL` records before the end of each partition to refill its replay buffer.
- **Resume:** `Last-Event-ID` (sent by `EventSource` on reconnect), else `?since=<event id>`. The hub keeps the
  last `ATLAS_GATEWAY_BUFFER` events ordered by stream position and deduplicates on the record id
  (the publisher is at-least-once). A client too slow to keep up is disconnected and resumes from its last id.
- **Operations:** `/healthz` (liveness), `/readyz` (a broker answered; false while draining), `/metrics`
  (Prometheus: `atlas_gateway_clients`, `atlas_gateway_events_consumed_total`, `atlas_gateway_events_sent_total`,
  `atlas_gateway_consumer_lag`, duplicates, decode errors, Go runtime), OpenTelemetry traces over OTLP when
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set (HTTP spans and Kafka spans via kotel), JSON logs (slog), graceful
  shutdown on SIGTERM (readiness off, streams closed so clients reconnect elsewhere, then HTTP drain).

| Variable | Default |
|---|---|
| `ATLAS_KAFKA_BOOTSTRAP` | `localhost:19092` |
| `ATLAS_FILINGS_TOPIC` | `atlas.filings.v1` |
| `ATLAS_GATEWAY_ADDR` | `:8090` |
| `ATLAS_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` |
| `ATLAS_GATEWAY_BUFFER` / `ATLAS_GATEWAY_CLIENT_BUFFER` / `ATLAS_GATEWAY_BACKFILL` | `1000` / `256` / `200` |
| `ATLAS_LOG_LEVEL` | `info` |

```bash
docker run --rm -v "$PWD":/src -w /src/services/realtime-gateway golang:1.27 go test -race ./...
docker build -t atlas/realtime-gateway services/realtime-gateway
```
