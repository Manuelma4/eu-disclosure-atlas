# Operations

How to run the lake and the pipeline locally: DuckLake catalog, Dagster, backfills and data-quality checks.
Design: [architecture.md](architecture.md) ("Lakehouse"), decisions [0005](decisions/0005-ducklake-as-lakehouse-format.md)
and [0007](decisions/0007-dagster-for-orchestration.md).

## 1. Choose a catalog

The lake reads its location from the environment (see `src/disclosure_atlas/lake.py`):

| Variable | Default | Meaning |
|---|---|---|
| `ATLAS_LAKE` | `lake` | Lake root: bronze files, pseudonym key, FX reference, default catalog and data |
| `ATLAS_CATALOG` | `<lake>/catalog.ducklake` | DuckLake metadata: a DuckDB file, `sqlite:<file>` or `postgres:<libpq string>` |
| `ATLAS_CATALOG_SCHEMA` | `ducklake` | PostgreSQL schema for the catalog tables |
| `ATLAS_DATA_PATH` | `<lake>/data/` | Parquet data files: a folder, or `s3://bucket/prefix/` |
| `ATLAS_S3_ENDPOINT`, `ATLAS_S3_KEY_ID`, `ATLAS_S3_SECRET`, `ATLAS_S3_REGION` | AWS credential chain, region `auto` | S3-compatible storage such as Cloudflare R2 |
| `ATLAS_SNAPSHOT_PUBLISHER` | `jsonl` | Where `snapshot.committed` goes: `jsonl` (`<lake>/events/snapshots.jsonl`), `log`, or `module:factory` |

The default DuckDB-file catalog can be opened by **one process at a time**: fine for tests, one-off CLI commands
or `dagster dev` alone. To run the API, Dagster and the CLI together, use PostgreSQL:

```bash
docker compose -p atlas-dev -f infra/compose.yaml up -d postgres
export ATLAS_CATALOG='postgres:host=127.0.0.1 port=5432 dbname=atlas user=atlas password=atlas-dev-only'
```

If another PostgreSQL already listens on port 5432, publish the container elsewhere with
`ATLAS_PG_PORT=55432 docker compose ...` and use `port=55432` in `ATLAS_CATALOG`. The `-p` project name keeps
your containers apart from other checkouts that use the same compose file.

## 2. Migrate an existing Parquet lake

Lakes created before DuckLake keep silver as Parquet files under `<lake>/silver/`. Load them once:

```bash
disclosure-atlas --lake lake migrate-lake      # one DuckLake commit per file; re-running skips migrated files
disclosure-atlas --lake lake snapshots         # snapshot ids, usable as ?as_of= in the API
disclosure-atlas --lake lake stats             # same gold numbers as before the migration
```

The Parquet files are left in place; delete `<lake>/silver/` once you are satisfied.

## 3. Run Dagster

```bash
pip install -e '.[dev,api,orchestration]'
export DAGSTER_HOME="$PWD/.dagster" && mkdir -p "$DAGSTER_HOME" && cp infra/dagster/dagster.yaml "$DAGSTER_HOME/"
export ATLAS_LAKE="$PWD/lake"
dagster dev                                    # UI on http://127.0.0.1:3000 (the code location is set in pyproject.toml)
```

What you get, for every registered connector (currently `se_fi_pdmr`):

- assets `bronze/<source>` → `silver/<source>`, partitioned by publication day in the regulator's time zone,
  and the shared `gold/<dataset>` assets, refreshed by the `gold_automation` sensor;
- schedule `<source>_business_hours` (Sweden: every 5 minutes, 07:00-20:00 Europe/Stockholm, Monday to Friday)
  and `<source>_nightly_settle` (06:30 local, re-ingests yesterday), both running by default;
- job `lake_maintenance` (weekly, Sunday 03:00 Paris): flushes inlined rows to Parquet and merges small files.

Every step that opens the catalog runs in the `ducklake` concurrency pool; `infra/dagster/dagster.yaml` limits it
to one, which a DuckDB-file catalog requires. With PostgreSQL you can raise `concurrency.pools.default_limit`.

Re-polling an unchanged day is cheap: bronze stores the response, and silver skips documents whose SHA-256 was
already committed by the same parser version (recorded in each snapshot's commit info). Bumping a connector's
`parser_version` makes silver re-parse stored bronze documents without refetching them.

## 4. Backfill a source

In the UI: **Catalog → silver/se_fi_pdmr → Materialize → select a partition range** (bronze is selected with it).
From the command line, for one partition:

```bash
dagster asset materialize -m disclosure_atlas.orchestration.definitions \
  --select 'bronze/se_fi_pdmr,silver/se_fi_pdmr' --partition 2026-10-16
```

For long ranges, prefer a UI backfill: it queues one run per partition and respects the pool limit. Each source
connector also keeps its own politeness rules (one request per second per host), so a ten-year backfill of a
source is slow by design.

The pipeline-free path still works: `disclosure-atlas ingest se_fi_pdmr --start 2026-04-20` writes the same
bronze and silver (one commit per document).

## 5. Check freshness and data quality

Each `silver/<source>` asset carries four checks, run after every materialization:

| Check | Passes when | Severity |
|---|---|---|
| `freshness` | the newest publication is at most `max_staleness_hours` old (96 h by default) | error |
| `volume` | the last weekday's count is within 10x of the 28-day weekday median | warning |
| `identifier_validity` | at least 98% of ISINs (ISO 6166) and LEIs (ISO 17442) pass their check digits | warning |
| `superseded_share` | at most 25% of records are superseded by the authority | warning |

See them under **Catalog → silver/<source> → Checks**, or run them alone with **Execute checks**. Thresholds live
in `src/disclosure_atlas/orchestration/regulators.py` (per regulator) and `quality.py`. Without Dagster, the same
numbers come from SQL:

```bash
disclosure-atlas query "SELECT source, max(published_at) AS newest, max(first_seen_at) AS last_ingested
                        FROM insider_transactions GROUP BY 1"
```

The API reports the same through `GET /v1/sources` (`last_published_at`, `last_ingested_at`) and
`GET /v1/health` (current snapshot).

## 6. Snapshots and time travel

- `X-Atlas-Snapshot` on every API response is the DuckLake snapshot the answer came from.
- `?as_of=<snapshot>` on `/v1/insider-transactions`, `/v1/insider-transactions/{id}`, `/v1/issuers` and
  `/v1/issuers/{lei}` answers from that snapshot (cached as immutable); unknown ids return 404.
- Each run that commits snapshots publishes `snapshot.committed` events through the configured
  `SnapshotPublisher`. To plug in a broker, implement `publish(event)` and point
  `ATLAS_SNAPSHOT_PUBLISHER=package.module:factory` at a factory taking the lake root.
