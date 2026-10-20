# Architecture

## Goals
- One schema for insider trades and short positions across every EEA authority.
- Full provenance: every gold value traces back to a raw document, its URL, its fetch time and its hash.
- Reproducible and cheap to run: files and SQL, no servers needed to build the dataset.
- Safe by design for personal data (see [legal-and-ethics.md](legal-and-ethics.md)).

## Layers

| Layer | Where | Content | Published |
|---|---|---|---|
| Bronze | `lake/bronze/<source>/YYYY/MM/` + `_manifest.jsonl` | Raw responses exactly as returned by the authority | Never |
| Silver | `lake/silver/<dataset>/<source>/*.parquet` | Typed records from one fetch, append-only | No (intermediate) |
| Gold | DuckDB views over silver | Latest version of each record, superseded rows removed, derived flags | Yes (Parquet export) |

Gold rules:
- **Latest wins:** for each `record_id`, the row from the most recent fetch is kept.
- **Revisions:** rows a source marks as revised or cancelled carry `is_superseded = true` and leave gold.
  Revisions can arrive after publication, so the daily job re-fetches a trailing window (30 days by default).
- **Derived flags:** `is_open_market` = buy or sell of shares on a trading venue.

## Connectors
A connector is one authority × one dataset in one module (`connectors/<iso2>_<authority>_<dataset>.py`),
registered with `@register` and discovered automatically, so parallel work never edits a shared list.

- `fetch(start, end)` performs the network calls and yields `RawDocument`s, each with metadata such as the
  date window, row count and truncation flag.
- `parse(doc, ctx)` is pure: it turns one raw document into validated records. Tests feed it synthetic
  documents with no network access.
- Sources that cap results, such as FI's 1,000-row export, are fetched in adaptive windows: a window that hits
  the cap is split in halves until it fits. A single day that still hits the cap is flagged as truncated
  and reported.
- `PoliteClient` identifies the project, keeps at most one request per second per host, and retries 429/5xx
  responses with back-off.

## Identity and stability
- `record_id` is a hash of the source's natural key (publication time, issuer, persons, instrument, date,
  volume, price, venue) plus an occurrence index for genuinely identical rows. Re-fetching the same data gives
  the same ids.
- Person ids are keyed HMACs scoped to the issuer, so they are stable within one issuer and unlinkable across
  issuers. The published dataset uses one key (`ATLAS_PSEUDONYM_KEY`, a CI secret). Ids are stable for a given
  key.

## AI-assisted extraction (WS1)
PDF-only sources use deterministic parsing first, because the EU notification template is standardised.
An LLM fallback handles layouts the parser cannot read:
- provider-agnostic interface: Claude API, or local models through Ollama so filings never leave the machine;
- JSON-schema-constrained output validated by the same Pydantic models, with per-field confidence and
  `parser = "llm:<model>@<prompt-version>"` provenance;
- an evaluation gate: a hand-verified gold set with field-level precision and recall, run in CI on every
  parser or prompt change, and a regression report against the previous run.

## Enrichment (WS5)
GLEIF for legal entities, ESMA FIRDS for instruments and issuers, OpenFIGI for tickers, and ECB reference
rates for EUR amounts. Enrichment lives in gold views, so silver stays a faithful image of each source.

## Publishing (WS8)
A daily GitHub Actions job ingests each source with a look-back window, runs data-quality checks (checksums,
date sanity, duplicates, freshness), compacts silver, and publishes gold as Parquet plus a DuckDB file.

## Why these choices
See the decision records in [decisions/](decisions/).
