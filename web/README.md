# EU Disclosure Atlas: website

Next.js 16.4 (React 19.3, Cache Components, Partial Prefetching), TypeScript, Tailwind CSS v4 and next-intl.

The website is a client of the public REST API, through a typed client generated from `openapi.json`.
Data components stream inside `<Suspense>` at request time and read through `"use cache"` functions tagged
`snapshot`, so `POST /api/revalidate` (called when the pipeline commits new data) refreshes every page at once.

```bash
npm install
npm run dev            # http://localhost:3000 (expects the API on http://127.0.0.1:8000)
npm run typecheck      # route types + tsc
npm run build
npm run api:types      # regenerate src/lib/api/schema.d.ts after the API changes
npm run i18n:check     # fr, es and de have exactly the keys and placeholders of messages/en.json
npx playwright test    # end-to-end tests against a fixture API (builds into .next-e2e)
npm run lighthouse     # Lighthouse budgets on the same fixture site
```

Configuration: see `.env.example`.

## Map
- `src/app/[locale]/`: every page under a locale prefix (`/en`, `/fr`, `/es`, `/de`); `src/proxy.ts` redirects
  unprefixed URLs by `Accept-Language`. Strings live in `messages/<locale>.json`; numbers and dates use
  `lib/format.ts` (`getFormat()` on the server, `useFormat()` in client components).
- `components/command-palette.tsx`: Ctrl/Cmd+K issuer search (`GET /v1/issuers?q=`), an ARIA combobox in a
  native `<dialog>`.
- `components/price-chart.tsx`: lightweight-charts close price with insider buy and sell markers from
  `GET /v1/issuers/{lei}/prices`; arrow keys, Page Up/Down and Home/End move a crosshair that is announced.
- `components/explorer/`: DuckDB-WASM and Mosaic vgplot over `GET /v1/exports/insider_transactions.parquet`.
  `scripts/copy-duckdb.mjs` self-hosts the WASM bundles and the Parquet extension in `public/duckdb/`.

## Dataviz conventions
Shared by every chart (`components/diverging-bars.tsx` is the reference): the validated `--buy`, `--sell` and
`--other` tokens in `globals.css`, thin marks, a legend for two or more series, tooltips on hover and on keyboard
focus, and a table view for every chart.

## Responsive and touch
- Below 768 px the header collapses into a menu button (search, sections, account, language); Escape closes it.
- Grid items may shrink (`globals.css`), wide tables scroll inside their card as focusable regions, and the filings
  table becomes stacked rows below 640 px.
- On touch screens controls are at least 44 px tall; chart tooltips open on tap.
- The explorer builds its plots at the card's width and rebuilds them when a card crosses 420 px.
- `node scripts/audit-pages.mjs <base-url> <out-dir> [lei] [--shots]` checks every page at 360 to 1440 px in en, fr
  and de: horizontal overflow, console errors, failed requests, and screenshots.

## End-to-end tests
`scripts/serve-fixture.mjs` writes a synthetic lake (`e2e/fixture_lake.py`, invented names only), serves it with
the real FastAPI app on port 8005 (synthetic prices) and the production build on port 3005. The suite covers the
home, search, company, filings and explore flows, translations, keyboard paths and axe scans in light and dark
mode. The `phone` (Pixel 7) and `tablet` (Galaxy Tab S4) projects run `e2e/responsive.spec.ts`: no horizontal
scroll on any page, the mobile menu, tap targets, tap tooltips and stacked tables. The million-row explorer
benchmark is opt-in: `FIXTURE_ROWS=1200000 npx playwright test explore-scale`.
