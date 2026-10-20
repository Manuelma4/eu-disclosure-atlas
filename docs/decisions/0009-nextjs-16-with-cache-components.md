# 0009: Next.js 16 with Cache Components for the website

Status: accepted (2026-10-20)

## Context
The site needs search-engine-friendly company pages (thousands of issuers), live sections, interactive analytics,
four languages, and freshness within seconds of a data commit.

## Options
1. **Next.js 16.4** (React 19.3): Server Components, streaming, Cache Components (`"use cache"`, `cacheLife`,
   `cacheTag`) with tag-based invalidation; the largest ecosystem.
2. TanStack Start 1.x: excellent type safety and Vite tooling; React Server Components still maturing there.
3. Astro: superb for content sites, less natural for app-like analytics.
4. A single-page app: poor for search engines on company pages.

## Decision
Next.js 16.4 with Cache Components. Data functions are cached with `"use cache"` and tagged with the data
snapshot; the pipeline's `snapshot.committed` event triggers `revalidateTag`, so pages are served from cache
and refreshed right after new data lands. Interactive analytics run client-side with DuckDB-WASM over the
published Parquet. TanStack Query and Table are still used inside the app.

## Consequences
- Static-fast pages with fresh data and no polling.
- Deployable on Vercel, or self-hosted with the Node adapter if needed.
