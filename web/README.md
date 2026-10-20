# EU Disclosure Atlas: website

Next.js 16.4 (React 19.3, Cache Components, Partial Prefetching), TypeScript and Tailwind CSS v4.

The website is a client of the public REST API, through a typed client generated from `openapi.json`.
Data components stream inside `<Suspense>` at request time and read through `"use cache"` functions tagged
`snapshot`, so `POST /api/revalidate` (called when the pipeline commits new data) refreshes every page at once.

```bash
npm install
npm run dev            # http://localhost:3000 (expects the API on http://127.0.0.1:8000)
npm run typecheck      # route types + tsc
npm run build
npm run api:types      # regenerate src/lib/api/schema.d.ts after the API changes
```

Configuration: see `.env.example`.
