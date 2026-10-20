# 0004: Python for data and API, TypeScript for the web, one codebase per language

Status: accepted (2026-10-20)

## Context
The platform needs connectors, PDF and LLM extraction, a lakehouse, econometrics, a public API and a
search-engine-friendly website. One person and a few parallel agents maintain it.

## Options
1. Microservices in several languages (Go API, Python workers, Node gateway).
2. TypeScript everywhere (Node API, workers in TypeScript).
3. **Python for data and API (one package, several deployables) plus TypeScript for the web.**

## Decision
Option 3. Python has the best data and AI ecosystem (DuckDB, Polars, Arrow, pdfplumber, LLM SDKs, statsmodels),
and the API shares the Pydantic models with the pipeline, so the OpenAPI document is generated from the same
types that validate the data. TypeScript and React are the best fit for a modern, search-engine-friendly web
app. The `api`, `pipeline`, `extractor` and `notifier` processes are separate deployables built from one Python
codebase: a modular monolith that scales per process.

## Consequences
- One schema definition, from ingestion to API to the generated TypeScript client.
- Two toolchains (uv/pip and npm) instead of four.
- A process can be extracted into its own service later if its scaling profile demands it.
