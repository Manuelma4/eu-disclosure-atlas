# Live-event delay

**Alert:** `AtlasLiveEventDelayBudgetBurn` (page, ticket).
**SLO:** 99% of live events reach connected clients within 5 seconds (realtime-gateway: from the Kafka record
timestamp; query API stream: from first ingestion). Owner: platform.

## Confirm
1. Grafana *Real time*: delivery delay by service, connected clients, events per minute.
2. *Kafka: consumer lag*: lag on the gateway's consumer group means it cannot keep up.
3. Gateway traces: the span from consume to fan-out; slow subscribers show as long write spans.

## Mitigate
- Consumer lag: scale the gateway (KEDA scales on lag), check partition count versus replicas.
- Many clients on one replica: spread connections (gateway HPA on `atlas_stream_clients`), confirm the
  ingress does not buffer SSE (`X-Accel-Buffering: no`).
- Upstream delay: if the change-feed publisher is late, follow [filing-freshness.md](filing-freshness.md).
- The query API's polling stream adds up to its poll interval (5 s) of delay by design; websites should use
  the realtime-gateway.
