# 0006: NATS JetStream for events and job queues

Status: superseded by [0011](0011-kafka-event-streaming-with-schema-registry.md) (2026-10-20)

## Context
Several components react to new data: the live feed, website cache invalidation, alerts, webhooks and the LLM
extraction workers. Some flows are fan-out events, others are jobs that need retries, back-off, concurrency
limits and dead letters.

## Options
1. Kafka or Redpanda: excellent for very high throughput, heavy to operate for thousands of events per day.
2. RabbitMQ: mature queues, weaker replayable streams.
3. Queues in PostgreSQL (SKIP LOCKED, pgmq): no new infrastructure, but no good fan-out to long-lived
   SSE connections.
4. **NATS 2.14 with JetStream**: subjects, persistent replayable streams, work-queue streams with explicit
   acknowledgements and redelivery, plus a key-value store, in one small binary with clients for Python and
   TypeScript.

## Decision
NATS JetStream with two streams: `EVENTS` (fan-out, limits retention, replayable) and `JOBS` (work queue,
max deliveries with back-off, dead-letter subject), plus a KV bucket for API rate limits. Messages use
CloudEvents-style envelopes and consumers are idempotent.

## Consequences
- The lake stays the source of truth: events can be regenerated from snapshots, so the broker is not a
  durability risk. Streams that carry jobs still use synchronous persistence settings.
- One more process to run (small), with a clear upgrade path to a 3-node cluster or a managed service.
