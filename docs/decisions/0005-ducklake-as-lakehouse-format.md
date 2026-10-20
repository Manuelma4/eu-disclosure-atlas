# 0005: DuckLake as the lakehouse format

Status: accepted (2026-10-20); migration planned in workstream P2

## Context
Ingestion commits small batches often (a few filings every few minutes across ~60 source feeds). Research needs
reproducible, citable versions of the data. Readers include our API, the browser (DuckDB-WASM) and external
researchers.

## Options
1. Plain Parquet files plus DuckDB views (M0): simple, but no snapshots, no transactions, many small files.
2. Apache Iceberg with a REST catalog: the broadest engine support, but metadata files make frequent small
   commits slow and costly, and it needs a catalog service.
3. Delta Lake: similar trade-offs, Spark-centric ecosystem.
4. **DuckLake 1.0** (April 2026): catalog in a SQL database (PostgreSQL), data as Parquet on object storage.

## Decision
DuckLake. The catalog lives in the PostgreSQL we already run. Commits are ACID across tables, small inserts
are inlined in the catalog until they are worth a file, and snapshots give time travel and a change feed
between versions. Data files stay Parquet, and deletion files are Iceberg-compatible Puffin files, so the data
remains open.

## Consequences
- Snapshot ids become first-class in the product: ETags, `?as_of=`, citations in research notes.
- `snapshot.committed` events come straight from commits, and `filing.published` events from the change feed.
- Engines other than DuckDB read the daily Parquet exports; an Iceberg REST export can be added if demand
  appears.
