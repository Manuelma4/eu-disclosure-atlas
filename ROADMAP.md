# Roadmap

Status: `[ ]` todo · `[~]` in progress · `[x]` done. Each workstream (WS) is sized for one Claude session
and owns its files, so several can run in parallel on separate branches (see CLAUDE.md).

## M0 Foundation `[x]`
- [x] Unified schemas, multilingual normalization, pseudonymization, polite HTTP client
- [x] Medallion lake on Parquet + DuckDB (bronze, silver, gold views), CLI, CI
- [x] First live source: Sweden FI PDMR register (`se_fi_pdmr`), with adaptive windows for the 1,000-row cap

## Platform (web, API, events, AI, operations)
Design: [docs/architecture.md](docs/architecture.md) and decision records 0004 to 0009.

### P1 Platform foundation `[x]`
- [x] REST API `/v1` (FastAPI): filings with filters and cursor pagination, issuers, overview, weekly/monthly
  flow, sources; OpenAPI 3.1 with a CI drift check; ETags and caching headers pinned to the data version;
  RFC 9457 errors; SSE live stream with resume
- [x] Gold enrichment: `amount_eur` (ECB reference rates, ASOF join), `first_seen_at`, `filing_lag_days`
- [x] Website (Next.js 16.4, Cache Components): home with live tape and insider flow, company pages, filings
  explorer, architecture page; typed client generated from OpenAPI; revalidation endpoint
- [x] `watch` command for near-real-time ingestion; Compose file with PostgreSQL 18 and NATS 2.14 for P2/P3

### P2 Lakehouse and orchestration `[ ]`
Owns: `src/disclosure_atlas/lake.py` (migration), `src/disclosure_atlas/orchestration/`, `infra/`.
- Migrate the lake to DuckLake 1.0 (PostgreSQL catalog, local disk in dev, R2 in prod) without changing gold
  schemas; snapshot ids become the API version; `?as_of=` time travel on the API.
- Dagster 1.13: partitioned assets per source and day, sensors during each regulator's business hours,
  backfills, asset checks (freshness, volume, checksums), and a `snapshot.committed` hook.

### P3 Events, alerts and accounts `[ ]`
Owns: `src/disclosure_atlas/events/`, `src/disclosure_atlas/notifier/`, web account pages.
- NATS JetStream `EVENTS` and `JOBS` streams; publish `snapshot.committed` and `filing.published` from the
  DuckLake change feed; the API stream and the website revalidation subscribe to them.
- Accounts (Better Auth: passkeys, magic links), watchlists, alert rules, API keys with rate limits,
  Standard Webhooks with retries and a delivery log, email and Web Push.

### P4 AI layer `[ ]`
Owns: `src/disclosure_atlas/ai/`, `evals/ask/`.
- Stateless MCP server (spec 2026-07-28): `search_issuers`, `insider_activity`, `short_interest`,
  `cluster_buys`, `filing_lag_stats`, read-only `sql`, and an MCP App rendering an issuer timeline.
- Ask the Atlas: natural-language questions to guarded read-only SQL (sqlglot), answers with their SQL and a
  chart, gated by a golden question set.

### P5 Website product `[ ]`
Owns: `web/`.
- Explorer with DuckDB-WASM and Mosaic over the published Parquet; company search; price chart with insider
  markers; research pages; API reference (Scalar); English, French, Spanish and German; accessibility and
  Lighthouse budgets; Playwright end-to-end tests.

### P6 Production and operations `[ ]`
Owns: `infra/`, `.github/workflows/deploy.yml`.
- Container images, Kamal 2 deploys to a small VM, the website on Vercel, Cloudflare CDN, DNS and R2;
  OpenTelemetry traces and metrics, error tracking, public status page with per-source freshness.

## M1 Insider trades across Europe

### WS1 France AMF: PDF filings + AI-assisted extraction `[ ]`
Owns: `connectors/fr_amf_pdmr.py`, `src/disclosure_atlas/extraction/`, `evals/fr_amf_pdmr/`,
`docs/sources/fr_amf_pdmr.md`, `tests/sources/test_fr_amf_pdmr.py`.
- Discover the BDIF search and document endpoints for "Déclarations des dirigeants" (now published in French
  and English); fetch the listing plus each PDF.
- Deterministic parser (pdfplumber) for the standard notification template; an LLM fallback behind a
  provider-agnostic interface (Claude API via `ANTHROPIC_API_KEY`, or local Ollama), with JSON-schema output
  and per-field confidence. The pipeline must work with no LLM configured.
- Evaluation: at least 50 hand-verified filings as a gold set (synthetic names), field-level precision and
  recall, and a CI gate on the deterministic parser.
- Done when: 90 days of AMF filings ingest end to end and the eval report is in `evals/fr_amf_pdmr/README.md`.

### WS2 Germany BaFin and Netherlands AFM `[ ]`
Owns: `connectors/de_bafin_pdmr.py`, `connectors/nl_afm_pdmr.py` and their docs and tests.
- BaFin's managers' transactions database (one-year window) and AFM's MAR 19 register (CSV export).
- Done when: both ingest the last 90 days with valid LEI/ISIN rates reported in the source notes.

### WS3 More jurisdictions `[ ]`
- Survey and implement where data is reachable: Denmark, Finland, Norway, Spain (CNMV), Italy (Consob),
  Belgium (FSMA), Austria (FMA), Ireland (CBI), Poland (KNF), Portugal (CMVM). Record dead ends in
  `docs/sources/README.md`.

## M2 Short positions `[ ]`

### WS4 Net short positions (SSR) `[ ]`
Owns: `connectors/*_shorts.py`, their docs and tests.
- Current and historic public net short positions from FR, DE, NL, ES, IT, IE, SE, then others.
- Holder names are legal entities (funds); keep them. Map issuers to LEI/ISIN where the source provides them.
- Done when: five jurisdictions ingest full available history into `net_short_positions`.

## M3 Reference data and enrichment `[ ]`

### WS5 Entity resolution and FX `[ ]`
Owns: `src/disclosure_atlas/reference/`, gold enrichment views, `docs/reference-data.md`.
- GLEIF (LEI to legal name, country), ESMA FIRDS (ISIN to issuer LEI, instrument), OpenFIGI (ISIN to tickers).
  ECB reference rates and `amount_eur` are already done in P1.
- Done when: gold exposes `issuers`, `instruments` and `insider_transactions_enriched` with `amount_eur`
  and a primary ticker where one exists.

## M4 Research `[ ]`

### WS6 Do European insiders beat the market? `[ ]`
Owns: `research/insider_event_study/`.
- Event study of open-market purchases and sales (CAR at +5, +20 and +60 days; calendar-time portfolios),
  split by role, size, cluster buys and country. Prices via the `research` extra.
- Done when: a reproducible notebook and a short paper-style write-up with honest limitations.

### WS7 Thresholds and regulation `[ ]`
Owns: `research/thresholds/`.
- Bunching of net short positions just below 0.5% across jurisdictions.
- Listing Act threshold change: disclosure volume before and after, across national threshold choices.
- Filing-lag compliance with the three-business-day rule, by country.

## M5 Publishing and access `[ ]`

### WS8 Open-data releases `[ ]`
Owns: `.github/workflows/release-data.yml`, `src/disclosure_atlas/publish/`.
- Daily Parquet and DuckDB releases with a Frictionless `datapackage.json`, data-quality report and freshness
  badge (the API, website and live ingestion moved to the platform workstreams above).

## M6 Integration `[ ]`

### WS9 Signals for MAYAROSERO Investments `[ ]`
- JSON feed: European insider cluster buys and crowded shorts, with methodology notes.
- Adapter PR in the MAYAROSERO Investments repo (separate repository and branch).
