import type { Metadata } from "next";

import { Card } from "@/components/ui";

export const metadata: Metadata = {
  title: "Architecture",
  description: "How the EU Disclosure Atlas is built: event-driven lakehouse, versioned data, REST, SSE and MCP.",
};

const REPO = "https://github.com/Manuelma4/eu-disclosure-atlas/blob/main";

type Node = { name: string; detail: string; live: boolean };
const LAYERS: { title: string; note: string; nodes: Node[] }[] = [
  {
    title: "Sources",
    note: "Public by law, scattered by design",
    nodes: [
      { name: "Finansinspektionen (SE)", detail: "CSV export, adaptive windows", live: true },
      { name: "AMF (FR)", detail: "PDF filings, AI extraction", live: false },
      { name: "BaFin · AFM · CNMV · Consob", detail: "One connector each", live: false },
      { name: "Reference data", detail: "ECB rates live; GLEIF, FIRDS, OpenFIGI next", live: true },
    ],
  },
  {
    title: "Data plane · Python 3.14",
    note: "One codebase, several deployables",
    nodes: [
      { name: "Connectors", detail: "Plugins, polite client, provenance", live: true },
      { name: "ingestion · Dagster 1.13", detail: "Partitioned assets, sensors, checks", live: false },
      { name: "extraction · Python", detail: "LLM fallback, autoscaled on Kafka lag", live: false },
      { name: "alerts · Go", detail: "Watchlists, webhooks, retry topics", live: false },
    ],
  },
  {
    title: "State",
    note: "The lake is the system of record",
    nodes: [
      { name: "Lakehouse", detail: "Bronze → silver → gold; DuckLake 1.0 next", live: true },
      { name: "Kafka 4 + schema registry", detail: "Change-feed events, retries, DLQ", live: false },
      { name: "PostgreSQL 18", detail: "DuckLake catalog, accounts (CloudNativePG)", live: false },
      { name: "Object storage", detail: "Parquet on Cloudflare R2", live: false },
    ],
  },
  {
    title: "Interfaces",
    note: "Same API for the website and everyone else",
    nodes: [
      { name: "REST /v1 · FastAPI", detail: "OpenAPI 3.1, ETags per snapshot", live: true },
      { name: "realtime-gateway · Go", detail: "SSE fan-out; Python stream live today", live: true },
      { name: "Website · Next.js 16.4", detail: "Cache Components, typed client", live: true },
      { name: "ai-service · MCP + gRPC", detail: "Ask the Atlas, stateless MCP, MCP Apps", live: false },
    ],
  },
];

const STACK = [
  ["Website", "Next.js 16.4, React 19.3, TypeScript, Tailwind CSS v4", "Search-friendly company pages, streaming, caches invalidated by data commits"],
  ["API", "FastAPI on Python 3.14, Pydantic v2, OpenAPI 3.1", "One schema from ingestion to the generated TypeScript client"],
  ["Analytics engine", "DuckDB 1.5, Parquet, DuckLake 1.0", "Snapshots, time travel and a change feed on files and SQL"],
  ["Orchestration", "Dagster 1.13", "Partitioned assets, backfills, data-quality checks, lineage"],
  ["Events", "Kafka 4 (KRaft), schema registry, CloudEvents", "Replayable events from the change feed, retry and dead-letter topics"],
  ["Services", "Python, Go and TypeScript microservices", "Each bounded context scales and fails on its own"],
  ["Platform", "Kubernetes, Helm, Argo CD, KEDA, OpenTofu (Scaleway, EU)", "GitOps, preview environments, autoscaling on lag"],
  ["Security", "Keycloak, Envoy Gateway, cosign, SBOM", "OIDC with passkeys, edge policies, signed supply chain"],
  ["AI", "Claude API or local Ollama, MCP", "Extraction fallback, natural-language questions, agent access, all evaluated"],
  ["Observability", "OpenTelemetry, Prometheus, Loki, Tempo, Grafana", "SLOs as code with burn-rate alerts and runbooks"],
];

const PRINCIPLES = [
  "The lake is the system of record; caches, events and search are rebuildable projections.",
  "Every read is pinned to a data snapshot, so answers are consistent, cacheable and citable.",
  "Components react to events, not to polling.",
  "The website uses the public API: no private shortcuts.",
  "Each language where it is strongest: Python for data, TypeScript for the web.",
  "Natural persons are pseudonyms everywhere outside the private raw layer.",
];

function Status({ live }: { live: boolean }) {
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-ink-3">
      <span
        aria-hidden="true"
        className={`size-1.5 rounded-full ${live ? "bg-live" : "border border-ink-3"}`}
      />
      {live ? "Live" : "Planned"}
    </span>
  );
}

export default function ArchitecturePage() {
  return (
    <div className="grid gap-8">
      <header className="max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight">Architecture</h1>
        <p className="mt-3 text-lg text-ink-2 text-pretty">
          An event-driven lakehouse behind a cache-friendly API. New filings flow from regulators to your screen in
          minutes, and every number can be reproduced from a versioned snapshot.
        </p>
      </header>

      <section aria-label="Containers" className="grid gap-3">
        {LAYERS.map((layer, index) => (
          <div key={layer.title}>
            <div className="grid gap-3 rounded-xl border border-hairline bg-surface p-4 lg:grid-cols-[12rem_1fr]">
              <div>
                <h2 className="font-semibold tracking-tight">{layer.title}</h2>
                <p className="mt-0.5 text-sm text-ink-3">{layer.note}</p>
              </div>
              <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {layer.nodes.map((node) => (
                  <li key={node.name} className="rounded-lg border border-hairline bg-page px-3 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium">{node.name}</p>
                      <Status live={node.live} />
                    </div>
                    <p className="mt-0.5 text-xs text-ink-3">{node.detail}</p>
                  </li>
                ))}
              </ul>
            </div>
            {index < LAYERS.length - 1 && (
              <p aria-hidden="true" className="py-1 text-center text-ink-3">
                ↓
              </p>
            )}
          </div>
        ))}
      </section>

      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <Card title="Stack" subtitle="Current versions, October 2026">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-ink-3">
                <tr className="border-b border-hairline">
                  <th className="py-2 pr-4 font-medium">Layer</th>
                  <th className="py-2 pr-4 font-medium">Choice</th>
                  <th className="py-2 font-medium">Why</th>
                </tr>
              </thead>
              <tbody>
                {STACK.map(([layer, choice, why]) => (
                  <tr key={layer} className="border-b border-hairline/60 align-top">
                    <td className="py-2 pr-4 font-medium">{layer}</td>
                    <td className="py-2 pr-4 text-ink-2">{choice}</td>
                    <td className="py-2 text-ink-3">{why}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card title="Principles">
          <ol className="grid list-decimal gap-2 pl-5 text-sm text-ink-2">
            {PRINCIPLES.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ol>
          <p className="mt-4 text-sm">
            <a className="underline decoration-hairline underline-offset-2 hover:text-ink" href={`${REPO}/docs/architecture.md`}>
              Full architecture
            </a>{" "}
            ·{" "}
            <a className="underline decoration-hairline underline-offset-2 hover:text-ink" href={`${REPO}/docs/decisions`}>
              Decision records
            </a>
          </p>
        </Card>
      </div>
    </div>
  );
}
