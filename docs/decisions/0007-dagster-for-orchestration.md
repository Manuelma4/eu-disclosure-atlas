# 0007: Dagster for orchestration

Status: accepted (2026-10-20); implemented in workstream P2 (see docs/operations.md)

## Context
About 60 source feeds (30 authorities, two datasets), each partitioned by publication date, polled every few
minutes during business hours, backfilled over years, followed by enrichment and gold models with
data-quality checks.

## Options
1. Cron plus scripts: no lineage, no backfills, no visibility.
2. Airflow: task-centric, heavier, less natural for partitioned data assets.
3. Prefect: flexible flows, but its model is centered on flows rather than data assets.
4. Temporal, Restate or DBOS (durable execution): excellent for business transactions, weaker for data
   lineage, partitions and backfills.
5. **Dagster 1.13**: software-defined assets with partitions, sensors, backfills, asset checks, lineage and
   a UI.

## Decision
Dagster. Each source becomes a partitioned asset chain (bronze → silver) feeding shared gold assets. Sensors
poll during each regulator's business hours, asset checks encode data quality (freshness, volume, checksums,
parser coverage), and backfills such as ten years of Swedish history are one command.

## Consequences
- The Dagster UI doubles as the operations console and as a portfolio showcase of the pipeline.
- Business-style flows such as webhook delivery stay on NATS work queues, not in Dagster.
