import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { Card, TableScroll } from "@/components/ui";
import { alternates } from "@/lib/seo";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("architecture");
  return { title: t("title"), description: t("metaDescription"), alternates: await alternates("/architecture") };
}

const REPO = "https://github.com/Manuelma4/eu-disclosure-atlas/blob/main";

type NodeKey = keyof IntlMessages["architecture"]["nodes"];
type IntlMessages = typeof import("../../../../messages/en.json");
// Product names stay in English in every language; `name` is a message key when it needs translating.
type Node = { name: string; nameKey?: NodeKey; detail: NodeKey; live: boolean };
const LAYERS: { id: keyof IntlMessages["architecture"]["layers"]; nodes: Node[] }[] = [
  {
    id: "sources",
    nodes: [
      { name: "Finansinspektionen (SE)", detail: "fi", live: true },
      { name: "AMF (FR)", detail: "amf", live: false },
      { name: "BaFin · AFM · CNMV · Consob", detail: "others", live: false },
      { name: "", nameKey: "referenceName", detail: "reference", live: true },
    ],
  },
  {
    id: "data",
    nodes: [
      { name: "", nameKey: "connectorsName", detail: "connectors", live: true },
      { name: "ingestion · Dagster 1.13", detail: "ingestion", live: false },
      { name: "extraction · Python", detail: "extraction", live: false },
      { name: "alerts · Go", detail: "alerts", live: false },
    ],
  },
  {
    id: "state",
    nodes: [
      { name: "", nameKey: "lakehouseName", detail: "lakehouse", live: true },
      { name: "", nameKey: "kafkaName", detail: "kafka", live: false },
      { name: "PostgreSQL 18", detail: "postgres", live: false },
      { name: "", nameKey: "storageName", detail: "storage", live: false },
    ],
  },
  {
    id: "interfaces",
    nodes: [
      { name: "REST /v1 · FastAPI", detail: "rest", live: true },
      { name: "realtime-gateway · Go", detail: "gateway", live: true },
      { name: "", nameKey: "websiteName", detail: "website", live: true },
      { name: "ai-service · MCP + gRPC", detail: "ai", live: false },
    ],
  },
];

// Product names stay as they are; `null` choices contain words and are translated (stackChoice.<id>).
const STACK: [keyof IntlMessages["architecture"]["stack"], string | null][] = [
  ["website", "Next.js 16.4, React 19.3, TypeScript, Tailwind CSS v4, next-intl"],
  ["explorer", null],
  ["api", "FastAPI on Python 3.14, Pydantic v2, OpenAPI 3.1"],
  ["engine", "DuckDB 1.5, Parquet, DuckLake 1.0"],
  ["orchestration", "Dagster 1.13"],
  ["events", null],
  ["services", null],
  ["platform", "Kubernetes, Helm, Argo CD, KEDA, OpenTofu (Scaleway, EU)"],
  ["security", "Keycloak, Envoy Gateway, cosign, SBOM"],
  ["ai", null],
  ["observability", "OpenTelemetry, Prometheus, Loki, Tempo, Grafana"],
];

const PRINCIPLES = ["p1", "p2", "p3", "p4", "p5", "p6"] as const;

export default async function ArchitecturePage() {
  const [t, tc] = await Promise.all([getTranslations("architecture"), getTranslations("common")]);

  const status = (live: boolean) => (
    <span className="inline-flex items-center gap-1 text-[11px] text-ink-3">
      <span aria-hidden="true" className={`size-1.5 rounded-full ${live ? "bg-live" : "border border-ink-3"}`} />
      {live ? tc("live") : tc("planned")}
    </span>
  );

  return (
    <div className="grid gap-8">
      <header className="max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-3 text-lg text-ink-2 text-pretty">{t("lead")}</p>
      </header>

      <section aria-label={t("containers")} className="grid gap-3">
        {LAYERS.map((layer, index) => (
          <div key={layer.id}>
            <div className="grid gap-3 rounded-xl border border-hairline bg-surface p-4 lg:grid-cols-[12rem_1fr]">
              <div>
                <h2 className="font-semibold tracking-tight">{t(`layers.${layer.id}.title`)}</h2>
                <p className="mt-0.5 text-sm text-ink-3">{t(`layers.${layer.id}.note`)}</p>
              </div>
              <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {layer.nodes.map((node) => (
                  <li key={node.detail} className="rounded-lg border border-hairline bg-page px-3 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium">{node.nameKey ? t(`nodes.${node.nameKey}`) : node.name}</p>
                      {status(node.live)}
                    </div>
                    <p className="mt-0.5 text-xs text-ink-3">{t(`nodes.${node.detail}`)}</p>
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
        <Card title={t("stackTitle")} subtitle={t("stackSubtitle")}>
          <TableScroll label={t("stackTitle")}>
            <table className="w-full min-w-[36rem] text-sm">
              <thead className="text-left text-ink-3">
                <tr className="border-b border-hairline">
                  <th scope="col" className="py-2 pr-4 font-medium">{t("layer")}</th>
                  <th scope="col" className="py-2 pr-4 font-medium">{t("choice")}</th>
                  <th scope="col" className="py-2 font-medium">{t("why")}</th>
                </tr>
              </thead>
              <tbody>
                {STACK.map(([id, choice]) => (
                  <tr key={id} className="border-b border-hairline/60 align-top">
                    <td className="py-2 pr-4 font-medium">{t(`stack.${id}.layer`)}</td>
                    <td className="py-2 pr-4 text-ink-2">
                      {choice ?? t(`stackChoice.${id as "services" | "explorer" | "events" | "ai"}`)}
                    </td>
                    <td className="py-2 text-ink-3">{t(`stack.${id}.why`)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        </Card>
        <Card title={t("principlesTitle")}>
          <ol className="grid list-decimal gap-2 pl-5 text-sm text-ink-2">
            {PRINCIPLES.map((p) => (
              <li key={p}>{t(`principles.${p}`)}</li>
            ))}
          </ol>
          <p className="mt-4 text-sm">
            <a className="underline decoration-hairline underline-offset-2 hover:text-ink" href={`${REPO}/docs/architecture.md`}>
              {t("fullArchitecture")}
            </a>{" "}
            ·{" "}
            <a className="underline decoration-hairline underline-offset-2 hover:text-ink" href={`${REPO}/docs/decisions`}>
              {t("decisions")}
            </a>
          </p>
        </Card>
      </div>
    </div>
  );
}
