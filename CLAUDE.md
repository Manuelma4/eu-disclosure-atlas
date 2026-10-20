# EU Disclosure Atlas: instructions for Claude sessions

Open, unified data on insider trading (MAR Article 19) and short selling (SSR Article 6) across European
markets, plus reproducible research on top of it. Read README.md first, then ROADMAP.md for your workstream.

## Commands
- Setup: `python3.14 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev,api,ai]'`
- Checks (run before every commit): `ruff check . && pytest -q`
- Live checks (hit real endpoints, opt-in): `pytest -m network -q`
- CLI: `disclosure-atlas sources | ingest <source> --start YYYY-MM-DD | watch <source> | reference fx | serve |
  openapi | mcp | stats | query "<SQL>" | export`
- API contract: after changing `src/disclosure_atlas/api/`, run
  `disclosure-atlas --lake /tmp/empty-lake openapi > web/openapi.json && (cd web && npm run api:types)`
  (CI fails if `web/openapi.json` drifts from the code).
- Website: `cd web && npm install && npm run dev | typecheck | build`; `npx eslint src`. Next.js 16.4 differs
  from older versions: read `web/node_modules/next/dist/docs/` before writing web code (see `web/AGENTS.md`).
- Local infrastructure: `docker compose -f infra/compose.yaml up -d` (PostgreSQL 18, Redpanda as the Kafka API).
- Go services: build and test with the local toolchain if present, otherwise in Docker
  (`docker run --rm -v "$PWD":/src -w /src/services/<name> golang:1.27 go test ./...`).

## Architecture in one minute
Platform design and decisions: docs/architecture.md and docs/decisions/. Code map:
- `src/disclosure_atlas/models.py`: the unified schemas (`InsiderTransaction`, `NetShortPosition`) and enums.
- `src/disclosure_atlas/connectors/<source>.py`: one module per authority and dataset; `@register` makes it
  discoverable. Connectors implement `fetch(start, end)` (network) and `parse(doc, ctx)` (pure).
- `src/disclosure_atlas/normalize.py`: shared parsing and multilingual vocabularies (roles, natures, instruments).
- `src/disclosure_atlas/privacy.py`: pseudonyms for natural persons; legal-entity detection.
- `src/disclosure_atlas/lake.py`: bronze (raw) -> silver (Parquet) -> gold (DuckDB views).
- `src/disclosure_atlas/api/`: FastAPI app (REST `/v1`, SSE `/v1/stream`), read model pinned to the lake version.
- `src/disclosure_atlas/ai/`: SQL guard, MCP server (`/mcp`, stdio) and Ask the Atlas (`/v1/ask`); evals in
  `evals/ask/`; see docs/ai.md.
- `src/disclosure_atlas/reference/`: reference data (ECB FX now; LEI, ISIN and tickers in WS5).
- `web/`: Next.js website; a client of the public API only.
- `services/<name>/`: Go services (realtime-gateway, alerts), each with its own go.mod, tests and Dockerfile.
- `contracts/`: event JSON Schemas (generated from the Pydantic models, never edited by hand) and Protobuf.
- `deploy/`, `infra/`: Helm charts, Argo CD, k3d, Compose and OpenTofu.
- Source naming: `<iso2 country>_<authority>_<dataset>`, e.g. `fr_amf_pdmr`, `de_bafin_shorts`.

## Rules
- English only: code, docs, commits, PRs.
- Git: commits authored by `Manuelma4 <71296266+Manuelma4@users.noreply.github.com>`, with no Co-Authored-By
  or Claude-Session trailers (the owner's explicit preference).
- One branch per workstream (see ROADMAP.md), PR to `main`, CI green. Do not push to `main` directly unless
  the owner asks.
- Stay in your lane: own your workstream's files. Changes to shared modules (`models.py`, `normalize.py`,
  `lake.py`, `privacy.py`) must be additive and minimal, and must keep every existing test passing.
- Every connector ships with: `docs/sources/<source>.md` (endpoint, format, coverage, terms, quirks),
  offline tests with synthetic fixtures under `tests/sources/`, and one `@pytest.mark.network` smoke test.
- Never commit real personal names (not even in fixtures). Natural persons exist only as pseudonyms in
  silver and gold. Bronze is never published.
- Crawl politely through `PoliteClient` (identifies the project, 1 request per second per host, retries),
  respect robots.txt and each site's terms, and document the terms in the source note.
- Facts about law, dates and thresholds must be checked against primary sources (EUR-Lex, ESMA, the
  authority's site) and cited in the docs.
- Not investment advice; never place orders.
