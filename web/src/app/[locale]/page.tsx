import { connection } from "next/server";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { DivergingBars } from "@/components/diverging-bars";
import { LiveTape } from "@/components/live-tape";
import { TimeAgo } from "@/components/time-ago";
import { Card, Skeleton, StatTile } from "@/components/ui";
import { Link } from "@/i18n/navigation";
import { getFlow, getOverview, getSources } from "@/lib/api/data";
import { getFormat, getLabels } from "@/lib/format-server";

const DATASET_JSON_LD = {
  "@context": "https://schema.org",
  "@type": "Dataset",
  name: "EU Disclosure Atlas",
  description:
    "Unified, pseudonymized records of managers' transactions (MAR Article 19) and net short positions (SSR " +
    "Article 6) published by European national competent authorities.",
  isAccessibleForFree: true,
  spatialCoverage: "European Economic Area",
  variableMeasured: ["Insider transactions", "Net short positions"],
  creator: { "@type": "Person", name: "Manuel David Maya Rosero", url: "https://manuelma4.github.io/" },
};

export default async function Home() {
  const t = await getTranslations("home");
  return (
    <>
      <section className="max-w-3xl">
        <p className="text-sm font-medium text-accent-ink">{t("kicker")}</p>
        <h1 className="mt-3 text-4xl font-semibold tracking-tight text-balance sm:text-5xl">{t("title")}</h1>
        <p className="mt-4 text-lg text-ink-2 text-pretty">{t("lead")}</p>
        <Link
          href="/explore"
          className="mt-5 inline-flex items-center gap-2 rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-2"
        >
          {t("exploreCta")} <span aria-hidden="true">→</span>
          <span className="hidden font-normal text-ink-3 sm:inline">· {t("exploreCtaNote")}</span>
        </Link>
      </section>

      <Suspense fallback={<HomeSkeleton />}>
        <HomeData />
      </Suspense>

      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(DATASET_JSON_LD) }} />
    </>
  );
}

function HomeSkeleton() {
  return (
    <div className="mt-10 grid gap-4">
      <Skeleton className="h-32" />
      <div className="grid gap-4 sm:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <Skeleton className="h-80" />
    </div>
  );
}

async function HomeData() {
  await connection();
  const [overview, flow, sources, t, tc, format, labels] = await Promise.all([
    getOverview(),
    getFlow("week", 182),
    getSources(),
    getTranslations("home"),
    getTranslations("common"),
    getFormat(),
    getLabels(),
  ]);
  const ratio =
    overview.open_market_sells_30d > 0 ? overview.open_market_buys_30d / overview.open_market_sells_30d : null;
  const live = sources.filter((s) => s.records > 0);
  const planned = [
    { country: "FR", authority: "AMF", status: t("inProgressAmf") },
    { country: "DE", authority: "BaFin", status: tc("planned") },
    { country: "NL", authority: "AFM", status: tc("planned") },
    { country: "ES", authority: "CNMV", status: tc("planned") },
    { country: "IT", authority: "Consob", status: tc("planned") },
  ];

  return (
    <div className="mt-10 grid gap-6">
      <section className="grid gap-4 lg:grid-cols-[1.3fr_2fr]">
        <div className="rounded-xl border border-hairline bg-surface px-6 py-5">
          <p className="text-sm text-ink-2">{t("bought30d")}</p>
          <p className="mt-2 text-5xl font-semibold tracking-tight" data-testid="buy-30d">
            {format.eur(overview.buy_eur_30d)}
          </p>
          <p className="mt-2 text-sm text-ink-3">
            {t.rich("against", {
              amount: format.eur(overview.sell_eur_30d),
              snapshot: overview.snapshot,
              when: () => <TimeAgo iso={overview.last_ingested_at} />,
              code: (chunks) => <code className="font-mono text-xs">{chunks}</code>,
            })}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <StatTile label={t("filings")} value={format.int(overview.transactions)} note={t("liveSources", { count: overview.sources })} />
          <StatTile label={t("issuers")} value={format.int(overview.issuers)} note={t("countries", { count: overview.countries })} />
          <StatTile
            label={t("buysPerSell")}
            value={ratio == null ? "–" : `${format.oneDecimal(ratio)} : 1`}
            note={t("buysSells", {
              buys: format.int(overview.open_market_buys_30d),
              sells: format.int(overview.open_market_sells_30d),
            })}
          />
          <StatTile label={t("late")} value={format.percent(overview.late_share_365d)} note={t("lateNote")} />
        </div>
      </section>

      <Card title={t("flowTitle")} subtitle={t("flowSubtitle")}>
        <DivergingBars
          granularity="week"
          data={flow.map((p) => ({
            period: p.period,
            buy: p.buy_eur ?? 0,
            sell: p.sell_eur ?? 0,
            buys: p.buys,
            sells: p.sells,
          }))}
        />
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title={t("latestTitle")} subtitle={t("latestSubtitle")}>
          <LiveTape initial={overview.latest} />
        </Card>
        <Card title={t("largestTitle")} subtitle={t("largestSubtitle")}>
          <ol className="divide-y divide-hairline">
            {overview.largest_buys_30d.map((row, index) => (
              <li key={row.id} className="grid grid-cols-[1.5rem_1fr_auto] items-baseline gap-3 py-2.5 text-sm">
                <span className="tabular text-ink-3">{index + 1}</span>
                <div className="min-w-0">
                  {row.issuer_lei ? (
                    <Link href={`/company/${row.issuer_lei}`} className="block truncate font-medium hover:underline">
                      {row.issuer_name}
                    </Link>
                  ) : (
                    <span className="block truncate font-medium">{row.issuer_name}</span>
                  )}
                  <span className="text-xs text-ink-3">
                    {labels.role(row.role)}
                    {row.notifier_name ? ` ${t("via", { name: row.notifier_name })}` : ""} ·{" "}
                    {t("traded", { date: format.date(row.transaction_date) })}
                  </span>
                </div>
                <span className="tabular font-medium">{format.eur(row.amount_eur)}</span>
              </li>
            ))}
          </ol>
        </Card>
      </div>

      <Card title={t("coverageTitle")} subtitle={t("coverageSubtitle")}>
        <div className="overflow-x-auto">
          <table className="tabular w-full text-sm">
            <thead className="text-left text-ink-3">
              <tr className="border-b border-hairline">
                <th scope="col" className="py-2 pr-4 font-medium">{t("country")}</th>
                <th scope="col" className="py-2 pr-4 font-medium">{t("regulator")}</th>
                <th scope="col" className="py-2 pr-4 font-medium">{t("status")}</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">{t("records")}</th>
                <th scope="col" className="py-2 text-right font-medium">{t("lastPublication")}</th>
              </tr>
            </thead>
            <tbody>
              {live.map((s) => (
                <tr key={s.name} className="border-b border-hairline/60">
                  <td className="py-2 pr-4">{format.country(s.country)}</td>
                  <td className="py-2 pr-4 text-ink-2">{s.authority}</td>
                  <td className="py-2 pr-4">
                    <span className="inline-flex items-center gap-1.5">
                      <span aria-hidden="true" className="size-2 rounded-full bg-live" /> {tc("live")}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-right">{format.int(s.records)}</td>
                  <td className="py-2 text-right text-ink-2">
                    <TimeAgo iso={s.last_published_at} />
                  </td>
                </tr>
              ))}
              {planned.map((p) => (
                <tr key={p.country} className="border-b border-hairline/60 text-ink-3">
                  <td className="py-2 pr-4">{format.country(p.country)}</td>
                  <td className="py-2 pr-4">{p.authority}</td>
                  <td className="py-2 pr-4">{p.status}</td>
                  <td className="py-2 pr-4 text-right">–</td>
                  <td className="py-2 text-right">–</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
