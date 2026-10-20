# realtime-gateway (Go)

Streams new filings from Kafka (`atlas.filings.v1`) to browsers as server-sent events on `GET /v1/stream`,
with the same frames as the Python API (`retry: 5000`, `id: <first_seen_at>|<record id>`, `event: filing`,
`data: <Transaction JSON>`, `: ping` every 15 s), so the website can switch between them with one variable.

- **Consumption:** franz-go, one consumer per replica without a group: each replica reads every partition,
  starting `ATLAS_GATEWAY_BACKFILL` records before the end of each partition to refill its replay buffer.
- **Resume:** `Last-Event-ID` (sent by `EventSource` on reconnect), else `?since=<event id>`. The hub keeps the
  last `ATLAS_GATEWAY_BUFFER` events ordered by stream position and deduplicates on the record id
  (the publisher is at-least-once). A client too slow to keep up is disconnected and resumes from its last id.
- **Operations:** `:8080` with `/healthz` (liveness), `/readyz` (a broker answered; false while draining) and
  `/metrics` (Prometheus): `atlas_stream_clients`, `atlas_stream_events_total` and
  `atlas_stream_event_delay_seconds` (Kafka record timestamp to SSE write; the live-event SLO in
  deploy/observability), plus `atlas_gateway_events_consumed_total`, `atlas_gateway_consumer_lag`, duplicates,
  decode errors and Go runtime metrics. OpenTelemetry traces and metrics over OTLP when
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set (`OTEL_EXPORTER_OTLP_PROTOCOL=grpc` or http/protobuf; HTTP spans and Kafka
  spans via kotel), JSON logs (slog), graceful shutdown on SIGTERM (readiness off, streams closed so clients
  reconnect elsewhere, then HTTP drain).

| Variable | Default |
|---|---|
| `ATLAS_KAFKA_BOOTSTRAP` (or `KAFKA_BROKERS`) | `localhost:19092` |
| `ATLAS_FILINGS_TOPIC` | `atlas.filings.v1` |
| `ATLAS_GATEWAY_ADDR` | `:8080` (8090 on the host in infra/compose.yaml) |
| `ATLAS_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` |
| `ATLAS_GATEWAY_BUFFER` / `ATLAS_GATEWAY_CLIENT_BUFFER` / `ATLAS_GATEWAY_BACKFILL` | `1000` / `256` / `200` |
| `ATLAS_LOG_LEVEL` | `info` |

```bash
docker run --rm -v "$PWD":/src -w /src/services/realtime-gateway golang:1.27 go test -race ./...
docker build -t atlas/realtime-gateway services/realtime-gateway
```
