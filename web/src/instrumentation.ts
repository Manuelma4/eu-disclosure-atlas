import { registerOTel } from "@vercel/otel";

// Traces for page renders, route handlers and fetches to the API, exported over OTLP to the Collector
// (deploy/observability). Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set; the standard OTEL_* variables apply.
// Trace context propagates to the query API so a page render and its DuckDB queries share one trace.
export function register() {
  if (!process.env.OTEL_EXPORTER_OTLP_ENDPOINT) return;
  registerOTel({
    serviceName: process.env.OTEL_SERVICE_NAME ?? "web",
    attributes: { "deployment.environment.name": process.env.ATLAS_ENVIRONMENT ?? "local" },
    instrumentationConfig: {
      fetch: { propagateContextUrls: [/^https?:\/\/(127\.0\.0\.1|localhost|query-api)(:\d+)?\//, /\/v1\//] },
    },
  });
}
