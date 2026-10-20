// Traces for page renders, route handlers and fetches to the API, exported over OTLP to the Collector
// (deploy/observability) with the standard OpenTelemetry Node.js SDK. Off unless OTEL_EXPORTER_OTLP_ENDPOINT is
// set; the standard OTEL_* variables apply. The undici instrumentation propagates W3C trace context to the query
// API, so a page render and its DuckDB queries share one trace.
export async function register() {
  if (!process.env.OTEL_EXPORTER_OTLP_ENDPOINT || process.env.NEXT_RUNTIME !== "nodejs") return;
  const [{ NodeSDK }, { OTLPTraceExporter }, { resourceFromAttributes }, { UndiciInstrumentation }] = await Promise.all([
    import("@opentelemetry/sdk-node"),
    import("@opentelemetry/exporter-trace-otlp-http"),
    import("@opentelemetry/resources"),
    import("@opentelemetry/instrumentation-undici"),
  ]);
  new NodeSDK({
    resource: resourceFromAttributes({
      "service.name": process.env.OTEL_SERVICE_NAME ?? "web",
      "deployment.environment.name": process.env.ATLAS_ENVIRONMENT ?? "local",
    }),
    traceExporter: new OTLPTraceExporter(),
    instrumentations: [new UndiciInstrumentation()],
  }).start();
}
