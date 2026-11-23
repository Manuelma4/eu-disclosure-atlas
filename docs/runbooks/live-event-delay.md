# Live-event delay

**Alert:** `AtlasLiveEventDelayBudgetBurn` (page, ticket).
**SLO:** 99% of recorded server-side SSE delay observations are within five
seconds. Owner: platform. [SLI specification](../../deploy/observability/slos/realtime.yaml)

The query API measures from the filing's `first_seen_at` after yielding the SSE
event. The gateway measures from the Kafka record timestamp after a live event
write and flush. The gateway resumes from its backlog without adding those
replayed events to the delay histogram. Both measurements end at the server.
A client-side observation is needed to measure receipt or rendering.

## Confirm

1. In Grafana *Real time: SSE clients and event delay*, use *Delivery delay p50 / p95*
   by service to identify the affected emitter. The five-second SLI sums histogram
   observations across services.
2. For gateway delay, check the consumer group in *Kafka: consumer lag* and compare
   its trend with the gateway delay. For query API delay, compare the polling and
   lake-query times with the stream measurements.

## Mitigate
- Consumer lag: scale the gateway (KEDA scales on lag), check partition count versus replicas.
- Many clients on one replica: spread connections (gateway HPA on `atlas_stream_clients`), confirm the
  ingress does not buffer SSE (`X-Accel-Buffering: no`).
- Upstream delay: if the change-feed publisher is late, follow [filing-freshness.md](filing-freshness.md).
- The query API's polling stream adds up to its poll interval (5 s) of delay by design; websites should use
  the realtime-gateway.
