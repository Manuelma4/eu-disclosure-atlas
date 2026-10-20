# Roadmap

Status: `[ ]` todo · `[~]` in progress · `[x]` done. Each workstream (WS) is sized for one Claude session
and owns its files, so several can run in parallel on separate branches (see CLAUDE.md).

## M0 Foundation `[x]`
- [x] Unified schemas, multilingual normalization, pseudonymization, polite HTTP client
- [x] Medallion lake on Parquet + DuckDB (bronze, silver, gold views), CLI, CI
- [x] First live source: Sweden FI PDMR register (`se_fi_pdmr`), with adaptive windows for the 1,000-row cap

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
- GLEIF (LEI to legal name, country), ESMA FIRDS (ISIN to issuer LEI, instrument), OpenFIGI (ISIN to tickers),
  ECB reference rates (`amount_eur`).
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

### WS8 Daily pipeline, releases and explorer `[ ]`
Owns: `.github/workflows/ingest.yml`, `site/`, `src/disclosure_atlas/api/`.
- Scheduled GitHub Actions ingestion with a 30-day look-back for revisions, data-quality report, silver
  compaction, Parquet + DuckDB releases, freshness badge.
- Read-only API (FastAPI) and a static explorer on GitHub Pages.

## M6 Integration `[ ]`

### WS9 Signals for MAYAROSERO Investments `[ ]`
- JSON feed: European insider cluster buys and crowded shorts, with methodology notes.
- Adapter PR in the MAYAROSERO Investments repo (separate repository and branch).
