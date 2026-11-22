# Source stale

**Alert:** `AtlasSourceStale` (ticket), labelled with `source`. Owner: data.
The rule compares the latest reported ingestion timestamp per source to the
current time. It requires a gap greater than 26 hours, evaluated Tuesday through
Friday in UTC, and a condition that persists for 30 minutes.
[Alert rule](../../deploy/observability/prometheus/rules/atlas-alerts.yaml) ·
[Prometheus weekday function](https://prometheus.io/docs/prometheus/latest/querying/functions/#day_of_week)

## Confirm

1. In Prometheus or Grafana, check that the source has an
   `atlas_source_last_ingested_seconds` series. If the series is missing, check
   telemetry and the source metrics before interpreting alert silence. The
   threshold comparison requires a timestamp sample.
2. Compare the latest ingested record with the upstream listing using the
   [source notes](../sources/README.md). A quiet source or a local holiday can
   explain the gap. If the upstream listing is ahead, inspect recent runs in
   Dagster or the watch process and follow the [freshness runbook](filing-freshness.md).
3. If several sources lose metrics together, check the
   [telemetry pipeline](telemetry-pipeline.md).

## Follow up

Record the upstream check, last observed ingestion time, and recent job outcome.
Filing freshness measures the delay of records that arrived. Use source activity
and connector state to investigate delays where no new records arrive.
