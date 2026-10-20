# Kafka consumer lag

**Alert:** `AtlasKafkaConsumerLagHigh` (ticket), labelled with `group`.
**Signal:** a consumer group is more than 1,000 records behind for 10 minutes. Owner: platform.
This is a placeholder until the P3 consumers (realtime-gateway, alerts) run in every environment.

## Confirm
1. Grafana *Kafka: consumer lag*: which group and topic, and whether lag grows or drains.
2. Consumer logs and traces: errors, retries, poison messages going to the dead-letter topic.

## Mitigate
- Slow consumer: scale out (KEDA ScaledObject on lag) up to the partition count.
- Poison message: let the retry topics and DLQ absorb it; never skip offsets by hand without a ticket.
- Broker trouble: Strimzi status (`kubectl -n kafka get kafka`), disk usage, under-replicated partitions.
