import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

// Security headers (P7, docs/security/threat-model.md). Bundles load from this origin only, each with a
// Subresource Integrity hash. Inline scripts carry the streamed React Server Components payload, which differs
// per render; nonces would force dynamic rendering of every page and defeat Cache Components, so inline
// scripts stay allowed and XSS defense relies on React escaping (no dangerouslySetInnerHTML) plus the tight
// object-src, base-uri, form-action, frame-ancestors and connect-src allowlists below. The explorer compiles
// DuckDB-WASM ('wasm-unsafe-eval', which allows WebAssembly only, not JavaScript eval) in a same-origin worker.
const isDev = process.env.NODE_ENV === "development";
const apiOrigin = new URL(process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://127.0.0.1:8000").origin;
const contentSecurityPolicy = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'${isDev ? " 'unsafe-eval'" : ""}`,
  "worker-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self'",
  `connect-src 'self' ${apiOrigin}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  ...(apiOrigin.startsWith("https:") ? ["upgrade-insecure-requests"] : []),
].join("; ");
const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), browsing-topics=()" },
];

const nextConfig: NextConfig = {
  // Standalone output for the container image (web/Dockerfile).
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  poweredByHeader: false,
  // End-to-end runs build against the fixture API into their own folder (see playwright.config.ts).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  experimental: {
    sri: { algorithm: "sha256" },
    globalNotFound: true,
  },
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      {
        // DuckDB-WASM bundles are immutable per package version.
        source: "/duckdb/:file*",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      },
    ];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default createNextIntlPlugin("./src/i18n/request.ts")(nextConfig);
