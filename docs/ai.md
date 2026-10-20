# AI layer: MCP server and Ask the Atlas

The AI layer lets agents and people query the Atlas in their own terms, with the same guarantees as the REST
API: read-only, gold views only, pseudonymous managers, reproducible answers. It has three parts, all in
`src/disclosure_atlas/ai/`:

| Part | What it is | Entry points |
|---|---|---|
| SQL guard | Static (sqlglot) and runtime (DuckDB sandbox) checks for untrusted SQL | `guard.py` |
| MCP server | Tools, a schema resource and an MCP App for agents, protocol 2026-07-28 | `/mcp` on the API, `disclosure-atlas mcp` (stdio) |
| Ask the Atlas | Question → SQL written by Claude → guard → rows → short answer | `POST /v1/ask` |

Install with the `ai` extra: `pip install -e '.[dev,api,ai]'`.

## Versions checked (October 2026)

- **MCP specification 2026-07-28**, the current revision: stateless requests (no `initialize` handshake, no
  session id), `server/discover`, and the MCP Apps extension (`io.modelcontextprotocol/ui`).
- **MCP Python SDK `mcp` 2.3.0** (2 October 2026). The 2.x line has supported 2026-07-28 since v2.0.0
  (28 July 2026, released with the specification); `mcp.types.LATEST_PROTOCOL_VERSION` is `2026-07-28`, and MCP
  Apps ships in the core package as `mcp.server.apps`. Release notes:
  <https://github.com/modelcontextprotocol/python-sdk/releases>; specification:
  <https://modelcontextprotocol.io/specification/latest>; Apps wire protocol:
  <https://github.com/modelcontextprotocol/ext-apps>.
- **Anthropic Python SDK `anthropic` 1.15.0** and the model **`claude-opus-5-5`** (Claude Opus 5.5, the current
  Opus). Override the model with `ATLAS_ASK_MODEL`.

## SQL guard

Every query from an agent (the `sql` tool) or from a model (Ask the Atlas) passes two independent layers:

1. **Static check** (`guard()`): sqlglot parses the text with the DuckDB dialect. It must be exactly one
   statement, a SELECT or a set operation of SELECTs (CTEs allowed). It may read only `insider_transactions`
   and `net_short_positions`: no silver tables, no `fx_rates`, no qualified names (`main.x`,
   `information_schema.x`), no table functions, no file paths. It may not call functions that read files or
   URLs, inspect the environment, settings or catalog, or run nested SQL (`read_*`, `*_scan`, `glob`, `getenv`,
   `current_setting`, `query`, `duckdb_*`, `pragma_*`, ...). `INTO`, `COPY`, `ATTACH`, `INSTALL`, `LOAD`, `SET`,
   `PRAGMA` and every write or DDL statement are rejected. A `LIMIT` is added, or clamped to 200 rows. The SQL
   that runs is regenerated from the syntax tree, so comments and formatting tricks never reach DuckDB.
2. **Runtime sandbox** (`SandboxedLake`): queries run on a DuckDB connection built by `Lake.connect()` and then
   locked: `allowed_directories` is the lake folder (the gold views read Parquet from it),
   `enable_external_access = false`, extension auto-install and auto-load off, and `lock_configuration = true`,
   so not even a guard bypass could read `/etc/passwd`, reach the network or turn access back on. A timer
   interrupts any query after 5 seconds.

Natural persons are pseudonymous ids in gold (see [decisions/0002](decisions/0002-pseudonymize-natural-persons.md)),
so no tool can return a manager's name. The tests in `evals/ask/test_ask_guard.py` cover injections, writes,
stacked statements, unknown tables, comments, file and network functions, the row cap, the timeout and the
sandbox itself.

## MCP server

`build_server(read_model)` in `mcp_server.py` returns an `MCPServer` with read-only tools (all annotated
`readOnlyHint`):

| Tool | Arguments | Returns |
|---|---|---|
| `search_issuers` | `query` (name, LEI or ISIN), `country`, `limit` | LEI, name, country, transaction counts |
| `insider_activity` | `lei`, `days` (default 365) | Open-market buys and sells (counts, EUR, distinct managers), net flow, monthly flow, recent transactions |
| `largest_insider_buys` | `days` (default 30), `country`, `limit` | Largest open-market purchases by managers, in EUR |
| `cluster_buys` | `days`, `window_days`, `min_insiders`, `country` | Issuers where several managers bought within a short window |
| `short_interest` | `issuer` (LEI, ISIN or name), `as_of` | Each holder's latest net short position (≥ 0.5%) and the disclosed total |
| `filing_lag_stats` | `country` | Mean, median, 90th percentile and late share of filing lags, per country |
| `sql` | `query` | Guarded read-only SELECT over the gold views: `{sql, columns, rows, truncated}` |
| `show_insider_flow` | `lei`, `days` (default 730) | Monthly flow for one issuer, rendered by the MCP App below |

The resource `atlas://schema` describes the queryable tables and columns (generated from the live views, so it
cannot drift). Filing lags are calendar days; MAR Article 19(1) gives managers three business days to notify
(Regulation (EU) No 596/2014, [EUR-Lex](https://eur-lex.europa.eu/eli/reg/2014/596/oj)), so "late" means more
than five calendar days, which is late under any weekday pattern, as on the website.

**MCP App.** `show_insider_flow` carries `_meta.ui.resourceUri = ui://disclosure-atlas/issuer-flow.html`. Hosts
that support MCP Apps render that resource (`text/html;profile=mcp-app`, in `ui/issuer_flow.html`) in a
sandboxed iframe: it performs the `ui/initialize` handshake over `postMessage`, receives the tool result via
`ui/notifications/tool-result` and draws monthly buys above the axis and sells below. It loads no external
script and declares empty CSP domain lists, so it needs no network access. Hosts without Apps get the same data
as structured JSON.

**Transports.**

- **Streamable HTTP, stateless:** the API mounts the server at `/mcp` (`disclosure-atlas serve`, then
  `http://127.0.0.1:8000/mcp`), in JSON-response mode. A 2026-07-28 client sends self-contained POSTs; older
  clients still work through the SDK's legacy stateless path. DNS-rebinding protection allows only the hosts in
  `ATLAS_MCP_ALLOWED_HOSTS` (default `127.0.0.1:*,localhost:*,[::1]:*`; set the public host name in production,
  where Envoy also applies rate limits and authentication, see [architecture.md](architecture.md)).
- **stdio:** `disclosure-atlas --lake /path/to/lake mcp`, for local hosts such as Claude Desktop.

Try it with the SDK client:

```python
import asyncio
from mcp import Client

async def main():
    async with Client("http://127.0.0.1:8000/mcp") as client:
        hits = await client.call_tool("search_issuers", {"query": "volvo"})
        print(hits.structured_content)

asyncio.run(main())
```

### Claude Desktop

Add the server to `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`, Windows:
`%APPDATA%\Claude\`), with absolute paths to the virtual environment and the lake, then restart Claude Desktop:

```json
{
  "mcpServers": {
    "eu-disclosure-atlas": {
      "command": "/absolute/path/to/eu-disclosure-atlas/.venv/bin/disclosure-atlas",
      "args": ["--lake", "/absolute/path/to/eu-disclosure-atlas/lake", "mcp"]
    }
  }
}
```

A deployed instance can instead be added as a remote connector with its URL (`https://<host>/mcp`).

## Ask the Atlas

`POST /v1/ask` with `{"question": "..."}` (3 to 500 characters) returns:

```json
{
  "question": "Which five issuers had the largest insider buying in 2025?",
  "sql": "SELECT issuer_name, SUM(amount_eur) AS buy_eur FROM insider_transactions WHERE ... LIMIT 5",
  "explanation": "Sums open-market purchases in EUR per issuer for 2025.",
  "columns": ["issuer_name", "buy_eur"],
  "rows": [["...", 11057052.5]],
  "truncated": false,
  "answer": "...",
  "model": "claude-opus-5-5",
  "prompt_version": "v1",
  "snapshot": "3f9c1a2b7d4e"
}
```

How it works (`ask.py`):

1. The system prompt (`prompts/ask_sql.v1.md`) carries the schema description of the gold views, today's date
   and the house rules (open-market buying means `is_open_market AND direction > 0`, money in `amount_eur`,
   companies by LEI, managers only as pseudonyms). Claude replies with structured output `{sql, explanation}`.
2. The guard validates the SQL and it runs on the sandboxed connection. If the guard or DuckDB rejects it, the
   error goes back to Claude once for a corrected query (the assistant turn is replayed unchanged).
3. A second, low-effort call (`prompts/ask_answer.v1.md`) writes a one-to-three sentence answer from the
   first 50 rows only.

Requests use the server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`),
so a safety-classifier refusal is re-run on Anthropic's recommended fallback model; a final refusal is a 422.
The system prompt is marked for prompt caching.

Errors are RFC 9457 problem details: **503** when `ANTHROPIC_API_KEY` is not set (or the `ai` extra is missing),
**422** when no valid query could be produced, **502** when the model provider fails. The website and other
clients can show the SQL next to the answer, so every answer is auditable.

Prompts are versioned files (`prompts/<name>.<version>.md`); `PROMPT_VERSION` in `ask.py` selects them and is
returned with every answer. A new prompt version ships only with an evaluation run (below).

## Evaluation

`evals/ask/` holds the golden set and the runner; see [evals/ask/README.md](../evals/ask/README.md). In short:
35 questions with reference SQL and expected results frozen on a deterministic, fictitious lake; the runner
reports execution accuracy overall and per category and exits non-zero below 85%. It is skipped when no key is
configured, so CI runs only the offline tests (guard, golden integrity, scorer).

## Limits and next steps

- Ask the Atlas answers from the gold views only; no prices, no market capitalization, no news.
- Rate limits and authentication for `/v1/ask` and `/mcp` belong to the edge (Envoy, P6/P7); the process has no
  per-client quota of its own.
- The SQL guard denylists dangerous functions and relies on the sandbox as a second wall; an allowlist of
  analytic functions would be stricter if the threat model requires it.
- Next: Langfuse traces with evaluation scores, a chart spec in the `/v1/ask` response for the website, and the
  filing explainer (see [architecture.md](architecture.md#ai-layer)).
