# Kafka consumer lag

`AtlasKafkaConsumerLagHigh` is a platform ticket. The [alert rule](../../deploy/observability/prometheus/rules/atlas-alerts.yaml)
fires for a consumer group when its lag stays above 1,000 records for ten minutes.
The expression groups by `group`. It reads Redpanda's group-lag metric or the
Kafka exporter's partition lag summed by group after copying `consumergroup`
to `group`.
A value of exactly 1,000 is below the alert condition. With neither metric
available, there is no matching time series.

## Confirm the group and metric source

1. Read the `group` label and check whether its lag is growing or draining.
   The [Kafka dashboard](../../deploy/observability/grafana/dashboards/kafka-lag.json)
   shows group totals. Check individual topic and partition contributions in
   the exporter or consumer tooling when needed.
2. Confirm that the expected metric source is being scraped. The local
   [Prometheus configuration](../../deploy/observability/prometheus/prometheus.yml)
   uses Redpanda's `/public_metrics` endpoint. Check the Kafka exporter for
   cluster lag data. A missing series leaves group lag unobserved.
3. Identify the consumer and check processing errors and restart history. The
   [alerts service](../../services/alerts/README.md) has distinct evaluator,
   delivery and retry groups. Check the delivery tier before interpreting its
   backlog, since delayed retries are intentional.

## Mitigate the affected consumer

- Check broker health and partition availability before changing capacity.
  Look for storage pressure and under-replicated partitions.
- Check the deployed settings for the affected consumer before scaling it.
  The [alerts chart](../../deploy/helm/alerts/values.yaml) enables KEDA for
  its configured groups. The [realtime gateway chart](../../deploy/helm/realtime-gateway/values.yaml)
  leaves KEDA disabled by default.
- For a delivery backlog, inspect the retry and dead-letter records and
  correct the underlying delivery problem. Preserve consumer offsets for
  normal retry and recovery. Confirm that the group lag drains after mitigation.
