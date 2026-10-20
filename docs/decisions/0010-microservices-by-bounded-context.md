# 0010: Microservices by bounded context

Status: accepted (2026-10-20); amends [0004](0004-python-for-data-and-api-typescript-for-web.md)

## Context
The platform is growing from a read API into ingestion, LLM extraction, real-time delivery, alerting, AI
question answering and accounts. These parts differ in scaling profile (batch, CPU, LLM latency and cost,
thousands of long-lived connections), failure modes and release cadence.

## Decision
Split by bounded context; each service owns its data and is deployed, scaled and versioned on its own.

| Service | Language | Owns | Talks through |
|---|---|---|---|
| `ingestion` | Python 3.14 + Dagster | Connectors, bronze/silver/gold, DuckLake commits | Publishes `filing.published`, `snapshot.committed` (Kafka) |
| `extraction` | Python | LLM extraction of PDF filings | Consumes `extraction.requested`, publishes `extraction.completed` |
| `query-api` | Python (FastAPI) | Public REST `/v1` read models | REST (OpenAPI 3.1), reads DuckLake snapshots |
| `ai-service` | Python | Ask the Atlas, filing explainer, MCP server | gRPC with server streaming (Protobuf, managed with buf), MCP |
| `realtime-gateway` | Go | Fan-out of live events to browsers | Consumes Kafka, serves SSE |
| `alerts` | Go | Watchlists, alert rules, notification delivery | Consumes `filing.published`; retry topics and a dead-letter topic |
| `web` | TypeScript (Next.js) | Website and backend-for-frontend | REST, OIDC |
| `identity` | Keycloak | Users, OIDC clients, API clients | OIDC / OAuth 2.0 |

The Python services share one library (the schemas, normalization and privacy code: a deliberate shared
kernel), so a schema change is one pull request with one test suite.

## Consequences
- Each service scales on its own signal: `extraction` and `alerts` on Kafka consumer lag (KEDA),
  `query-api` on CPU behind a CDN, `realtime-gateway` on open connections.
- Contracts are explicit and checked in CI: OpenAPI drift, JSON Schema compatibility for events, `buf breaking`
  for Protobuf.
- More moving parts, which is accepted because each part has a distinct reason to exist. A service mesh is not
  added yet: mTLS can come from the platform layer when the number of services justifies it.
