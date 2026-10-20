# 0001: Medallion lake on Parquet files and DuckDB

Status: accepted (2026-10-20)

## Context
The dataset is append-heavy, small to medium in size (millions of rows, not billions), and must be rebuilt and
published by anyone, including a scheduled CI job. Sources revise past records.

## Decision
Bronze keeps raw responses as files with a JSONL manifest. Silver is append-only Parquet per source and fetch.
Gold is a set of DuckDB views that keep the latest version of each record and drop superseded ones.

## Consequences
- No server to operate. The whole lake is files, so CI can build it and releases can ship it.
- Revisions are handled by re-fetching a trailing window and "latest fetch wins".
- Many small Parquet files accumulate, so a compaction step is needed for publishing (WS8).
- A database server (PostgreSQL) or Spark can be added later if an API needs it. The gold views define the
  contract either way.
