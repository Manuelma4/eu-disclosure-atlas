"""Dagster definitions: one partitioned asset chain per registered connector, discovered from the registry.

    bronze/<source>  (daily partitions by publication date)  fetch raw documents, store them in bronze
        -> silver/<source>                                  parse, one DuckLake commit per document
            -> gold/<dataset>                               gold views (shared by every source of the dataset)

Each source gets a business-hours schedule in its regulator's time zone, a nightly settle run for the previous
day, and asset checks (freshness, volume, ISIN/LEI validity, superseded share). Every committed snapshot is
reported through the snapshot hook. Run with ``dagster dev`` from the repository root (see docs/operations.md).
"""

import os
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import dagster as dg

from .. import connectors
from ..connectors.base import Connector, ParseContext
from ..lake import Lake
from ..models import Dataset
from ..pipeline import provenance
from . import quality
from .regulators import Regulator, regulator_for
from .signals import signal_definitions
from .snapshots import SnapshotCommitted, SnapshotPublisher, publisher_from_env

POOL = "ducklake"
"""Concurrency pool of every step that opens the DuckLake catalog (limit it to 1 with a DuckDB-file catalog)."""


class LakeResource(dg.ConfigurableResource):
    """The lake; catalog and data path come from ATLAS_CATALOG and ATLAS_DATA_PATH (see lake.py)."""

    root: str = "lake"

    def get(self) -> Lake:
        return Lake(self.root)


class SnapshotPublisherResource(dg.ConfigurableResource):
    """Where `snapshot.committed` events go; chosen by ATLAS_SNAPSHOT_PUBLISHER (see snapshots.py)."""

    lake_root: str = "lake"

    def get(self) -> SnapshotPublisher:
        return publisher_from_env(Path(self.lake_root))


@dg.success_hook(required_resource_keys={"lake", "snapshot_publisher"})
def snapshot_committed(context: dg.HookContext) -> None:
    """Publish one `snapshot.committed` event per DuckLake snapshot the step reported in its output metadata."""
    ids: list[int] = []
    for metadata in (context.op_output_metadata or {}).values():
        value = metadata.get("snapshot_ids") if isinstance(metadata, dict) else None
        ids += list(getattr(value, "value", value) or [])
    if not ids:
        return
    lake: Lake = context.resources.lake.get()
    publisher: SnapshotPublisher = context.resources.snapshot_publisher.get()
    wanted = set(ids)
    for snapshot in lake.snapshots(since=min(ids) - 1):
        if snapshot["snapshot_id"] in wanted:
            publisher.publish(SnapshotCommitted.from_lake(snapshot, run_id=context.run_id))
    context.log.info(f"published snapshot.committed for {sorted(wanted)}")


def _silver(context: dg.AssetExecutionContext, lake: Lake, connector: Connector) -> dg.MaterializeResult:
    """Parse this partition's newest bronze documents; documents already committed by this parser are skipped."""
    key = context.partition_key
    entries = [e for e in lake.bronze_manifest(connector.name) if (e.get("meta") or {}).get("partition") == key]
    runs = [e["meta"].get("run_id") for e in entries]
    run = context.run.run_id if context.run.run_id in runs else (runs[-1] if runs else None)
    done = lake.parsed_documents(connector.name)
    ctx = ParseContext(pseudonym_key=lake.pseudonym_key())
    snapshot_ids: list[int] = []
    records = skipped = superseded = 0
    for entry in (e for e in entries if e["meta"].get("run_id") == run):
        if (entry["sha256"], connector.parser_id) in done:
            skipped += 1
            continue
        doc = lake.read_bronze(entry)
        parsed = list(connector.parse(doc, ctx))
        snapshot = lake.write_silver(
            connector.dataset, connector.name, parsed,
            provenance=provenance(connector, lake, doc, lake.root / entry["path"]) | {"partition": key},
        )
        if snapshot is not None:
            snapshot_ids.append(snapshot)
        records += len(parsed)
        superseded += sum(1 for r in parsed if r.is_superseded)
    return dg.MaterializeResult(metadata={
        "records": records,
        "superseded": superseded,
        "documents_unchanged": skipped,
        "snapshot_ids": snapshot_ids,
        "snapshot": lake.version(),
    })


def source_definitions(cls: type[Connector]) -> dg.Definitions:
    """Assets, checks, job and schedules of one connector."""
    name = cls.name
    regulator: Regulator = regulator_for(cls.jurisdiction)
    partitions = dg.DailyPartitionsDefinition(
        start_date=regulator.history_start, timezone=regulator.timezone, end_offset=1
    )
    tags = {"jurisdiction": cls.jurisdiction, "dataset": cls.dataset.value}
    retry = dg.RetryPolicy(max_retries=3, delay=30, backoff=dg.Backoff.EXPONENTIAL)

    @dg.asset(
        key=["bronze", name], partitions_def=partitions, group_name=name, kinds={"python"}, tags=tags,
        retry_policy=retry, description=f"Raw responses from {cls.authority} ({cls.homepage}), by publication date.",
    )
    def bronze(context: dg.AssetExecutionContext, lake: LakeResource) -> dg.MaterializeResult:
        day = date.fromisoformat(context.partition_key)
        store = lake.get()
        documents = size = truncated = 0
        for doc in cls().fetch(day, day):
            doc = replace(doc, meta={**doc.meta, "partition": context.partition_key, "run_id": context.run.run_id})
            store.write_bronze(doc)
            documents += 1
            size += len(doc.content)
            truncated += bool(doc.meta.get("truncated"))
        if truncated:
            context.log.warning(f"{truncated} windows hit the source row cap; data may be incomplete")
        return dg.MaterializeResult(metadata={"documents": documents, "bytes": size, "truncated_windows": truncated})

    @dg.asset(
        key=["silver", name], deps=[bronze], partitions_def=partitions, group_name=name, kinds={"ducklake"},
        tags=tags, pool=POOL, retry_policy=retry, description=f"Typed {cls.dataset.value} records from {name}.",
    )
    def silver(context: dg.AssetExecutionContext, lake: LakeResource) -> dg.MaterializeResult:
        return _silver(context, lake.get(), cls())

    def check(check_name: str, description: str, severity: dg.AssetCheckSeverity):
        def decorator(measure):
            @dg.asset_check(asset=silver, name=check_name, description=description, pool=POOL, blocking=False)
            def _check(lake: LakeResource) -> dg.AssetCheckResult:
                con = lake.get().connect()
                try:
                    result: quality.Measure = measure(con)
                finally:
                    con.close()
                return dg.AssetCheckResult(
                    passed=result.passed, severity=severity, description=result.description, metadata=result.metadata
                )

            return _check

        return decorator

    @check("freshness", f"Newest publication at most {regulator.max_staleness_hours:g} h old.",
           dg.AssetCheckSeverity.ERROR)
    def freshness(con):
        return quality.freshness(con, cls.dataset, name, regulator.max_staleness_hours)

    @check("volume", "Last weekday's record count within 10x of the 28-day weekday median.",
           dg.AssetCheckSeverity.WARN)
    def volume(con):
        return quality.volume(con, cls.dataset, name)

    @check("identifier_validity", "At least 98% of ISINs and LEIs pass their check digits.",
           dg.AssetCheckSeverity.WARN)
    def identifier_validity(con):
        return quality.identifier_validity(con, cls.dataset, name)

    @check("superseded_share", "At most 25% of records are superseded by the authority.",
           dg.AssetCheckSeverity.WARN)
    def superseded_share(con):
        return quality.superseded_share(con, cls.dataset, name)

    job = dg.define_asset_job(
        f"{name}_job", selection=dg.AssetSelection.assets(bronze, silver),
        hooks={snapshot_committed}, tags=tags, description=f"Ingest one publication day of {name}.",
    )

    @dg.schedule(
        cron_schedule=regulator.business_hours_crons(), job=job, execution_timezone=regulator.timezone,
        name=f"{name}_business_hours", default_status=dg.DefaultScheduleStatus.RUNNING,
        description=f"Every {regulator.every_minutes} min, {regulator.first_hour:02d}:00-{regulator.last_hour:02d}:00 "
        f"{regulator.timezone}, days {regulator.weekdays}: today's partition.",
    )
    def business_hours(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
        return dg.RunRequest(partition_key=_local_day(context, regulator, 0))

    @dg.schedule(
        cron_schedule="30 6 * * *", job=job, execution_timezone=regulator.timezone, name=f"{name}_nightly_settle",
        default_status=dg.DefaultScheduleStatus.RUNNING,
        description="Every day at 06:30 local time: re-ingest yesterday (late publications, revisions).",
    )
    def nightly(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
        key = _local_day(context, regulator, 1)
        return dg.RunRequest(run_key=f"{name}-settle-{key}", partition_key=key)

    return dg.Definitions(
        assets=[bronze, silver], asset_checks=[freshness, volume, identifier_validity, superseded_share],
        jobs=[job], schedules=[business_hours, nightly],
    )


def _local_day(context: dg.ScheduleEvaluationContext, regulator: Regulator, days_back: int) -> str:
    when: datetime = context.scheduled_execution_time.astimezone(ZoneInfo(regulator.timezone))
    return (when.date() - timedelta(days=days_back)).isoformat()


def gold_asset(dataset: Dataset, upstream: list[dg.AssetKey]) -> dg.AssetsDefinition:
    """Gold view of one dataset; records row counts and the snapshot whenever a silver partition changes."""

    @dg.asset(
        key=["gold", dataset.value], deps=upstream, group_name="gold", kinds={"duckdb"}, pool=POOL,
        # Eager, except that the many never-materialized history partitions upstream must not block it.
        automation_condition=dg.AutomationCondition.eager().without(~dg.AutomationCondition.any_deps_missing()),
        description=f"Gold `{dataset.value}` view: latest version per record, superseded rows removed.",
    )
    def gold(lake: LakeResource) -> dg.MaterializeResult:
        store = lake.get()
        con = store.connect()
        try:
            rows, sources = con.execute(f"SELECT count(*), count(DISTINCT source) FROM {dataset.value}").fetchone()
        finally:
            con.close()
        return dg.MaterializeResult(metadata={"rows": rows, "sources": sources, "snapshot": store.version()})

    return gold


@dg.op(pool=POOL)
def maintain_lake(lake: LakeResource) -> None:
    """Flush inlined rows to Parquet and merge small files (snapshots are kept: they are citable)."""
    lake.get().maintain()


@dg.job(description="Weekly DuckLake maintenance.")
def lake_maintenance() -> None:
    maintain_lake()


def build_definitions(lake_root: str | None = None) -> dg.Definitions:
    root = lake_root or os.environ.get("ATLAS_LAKE", "lake")
    sources = connectors.all_connectors()
    maintenance = dg.ScheduleDefinition(
        name="lake_maintenance_weekly", job=lake_maintenance, cron_schedule="0 3 * * 0",
        execution_timezone="Europe/Paris", default_status=dg.DefaultScheduleStatus.RUNNING,
    )
    gold_automation = dg.AutomationConditionSensorDefinition(
        "gold_automation", target=dg.AssetSelection.groups("gold"), default_status=dg.DefaultSensorStatus.RUNNING,
        minimum_interval_seconds=60, description="Refresh gold assets after any silver partition changes.",
    )
    shared: dict[str, Any] = {
        "assets": [
            gold_asset(dataset, [dg.AssetKey(["silver", n]) for n, c in sources.items() if c.dataset is dataset])
            for dataset in Dataset
        ],
        "jobs": [lake_maintenance],
        "schedules": [maintenance],
        "sensors": [gold_automation],
        "resources": {"lake": LakeResource(root=root), "snapshot_publisher": SnapshotPublisherResource(lake_root=root)},
    }
    return dg.Definitions.merge(
        *(source_definitions(cls) for cls in sources.values()), signal_definitions(LakeResource),
        dg.Definitions(**shared),
    )


defs = build_definitions()
