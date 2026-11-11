# 0008: REST, SSE and MCP as public interfaces, with caching keyed to data snapshots

Status: accepted (2026-10-20)

## Context
Consumers are browsers, apps, researchers and AI agents. Data changes in discrete commits, a few times an hour.

## Decision
- **REST `/v1`** with OpenAPI 3.1 generated from the models, cursor pagination, RFC 9457 errors and API keys.
  GraphQL is not used: arbitrary query shapes defeat CDN caching, and heavy analytical needs are better served
  by bulk Parquet and SQL.
- **Caching:** the cacheable data reads include the snapshot version in their ETag. Current responses use bounded
  cache lifetimes, and the website's Cache Components use the version as a cache tag.
  Issuer queries with `?as_of=<snapshot>` measure their 90-day and 365-day windows from the snapshot's
  commit timestamp. Live issuer, overview and flow responses also fingerprint their content in the ETag.
  Revalidation recomputes the live result so a record leaving a rolling window can invalidate the ETag
  while the snapshot stays fixed. Issuer and overview queries break ordering ties before applying limits
  or fingerprinting a list. If several issuer filings share the latest timestamp, the greatest record ID
  selects the reported name and country. The pinned issuer ETag version changes with the representation.
  Overview totals and largest purchases use the same database time for their 30-day window.
- **SSE** for live filings: one-way, works through proxies and CDNs, resumes with `Last-Event-ID`.
  WebSockets are not needed for a one-way feed.
- **Webhooks** following the Standard Webhooks spec for server-to-server notifications.
- **MCP** (spec 2026-07-28, stateless) so AI agents can use the dataset with the same guardrails as the API.

## Consequences
- The CDN absorbs most read traffic; API replicas stay small and stateless.
- Third parties get the same API the website uses, so it is always exercised and documented.
