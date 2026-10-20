# 0008: REST, SSE and MCP as public interfaces, with caching keyed to data snapshots

Status: accepted (2026-10-20)

## Context
Consumers are browsers, apps, researchers and AI agents. Data changes in discrete commits, a few times an hour.

## Decision
- **REST `/v1`** with OpenAPI 3.1 generated from the models, cursor pagination, RFC 9457 errors and API keys.
  GraphQL is not used: arbitrary query shapes defeat CDN caching, and heavy analytical needs are better served
  by bulk Parquet and SQL.
- **Caching:** every read is pinned to a data version, the `ETag` is that version, and responses carry
  `Cache-Control: public, s-maxage=60, stale-while-revalidate=600`. A new snapshot changes every ETag at once,
  and the website's Cache Components use the same version as a cache tag.
- **SSE** for live filings: one-way, works through proxies and CDNs, resumes with `Last-Event-ID`.
  WebSockets are not needed for a one-way feed.
- **Webhooks** following the Standard Webhooks spec for server-to-server notifications.
- **MCP** (spec 2026-07-28, stateless) so AI agents can use the dataset with the same guardrails as the API.

## Consequences
- The CDN absorbs most read traffic; API replicas stay small and stateless.
- Third parties get the same API the website uses, so it is always exercised and documented.
