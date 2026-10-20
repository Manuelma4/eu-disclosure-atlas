# 0011: Kafka event streaming with a schema registry and change-feed publishing

Status: accepted (2026-10-20); supersedes [0006](0006-nats-jetstream-for-events-and-jobs.md)

## Context
With services split by bounded context (0010), events become the integration backbone: new filings drive the
live feed, alerts, cache invalidation, extraction jobs and stream analytics such as cluster-buy detection.
Consumers need replay, independent offsets, schema evolution rules and delivery guarantees.

## Decision
- **Apache Kafka 4.x (KRaft, no ZooKeeper)**, Strimzi on Kubernetes, and Redpanda (Kafka API) on laptops.
- **Event format:** CloudEvents 1.0 envelopes (`id`, `source`, `type`, `time`, `dataschema`) with JSON
  payloads. The payload JSON Schemas are generated from the Pydantic models, committed under
  `contracts/events/`, registered in the schema registry with BACKWARD compatibility, and checked in CI.
- **Topics:** `atlas.filings.v1` (key: issuer LEI, so each issuer's events stay ordered), `atlas.snapshots.v1`,
  `atlas.extraction.requests.v1` / `.results.v1`, `atlas.notifications.v1` with `*.retry.5m`, `*.retry.1h` and
  `*.dlq` topics for delivery retries.
- **No dual writes:** events are published from the lake's change feed, the difference between two committed
  snapshots, with a checkpoint stored after publishing. A crash can only re-publish, never lose. Producers are
  idempotent and consumers deduplicate on the event `id` (the record id).
- **Stream processing:** real-time signals (for example three or more insiders of one issuer buying within
  30 days) run as a stateful consumer over `atlas.filings.v1` and publish `atlas.signals.v1`.

## Consequences
- Replayable history for new consumers and backfills; per-consumer lag is the autoscaling signal (KEDA).
- Kafka is heavier than NATS. The cost is accepted for replay, schema governance, the ecosystem
  (Strimzi, KEDA, Kafka Connect) and how widely it is used in industry.
- Event schemas become public contracts with versioned names (`.v1`).
