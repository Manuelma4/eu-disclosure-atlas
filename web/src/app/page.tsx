import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { DivergingBars } from "@/components/diverging-bars";
import { LiveTape } from "@/components/live-tape";
import { TimeAgo } from "@/components/time-ago";
import { Card, Skeleton, StatTile } from "@/components/ui";
import { getFlow, getOverview, getSources } from "@/lib/api/data";
import { countryName, formatDate, formatEur, formatInt, formatPercent, roleLabel } from "@/lib/format";

const PLANNED = [
  { country: "FR", authority: "AMF", status: "In progress: PDF filings with AI extraction" },
  { country: "DE", authority: "BaFin", status: "Planned" },
  { country: "NL", authority: "AFM", status: "Planned" },
  { country: "ES", authority: "CNMV", status: "Planned" },
  { country: "IT", authority: "Consob", status: "Planned" },
];

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

export default function Home() {
  return (
    <>
      <section className="max-w-3xl">
        <p className="text-sm font-medium text-accent">Insider trades · short positions · 30 regulators, one dataset</p>
        <h1 className="mt-3 text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
          Europe&rsquo;s insider trades, in one live, open dataset.
        </h1>
        <p className="mt-4 text-lg text-ink-2 text-pretty">
          EU law makes managers&rsquo; trades and large short positions public, but they are scattered across about
          thirty national regulators, in PDFs, spreadsheets and portals. The Atlas collects them, puts them in one
          schema and replaces every natural person with a pseudonym.
        </p>
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
  const [overview, flow, sources] = await Promise.all([getOverview(), getFlow("week", 182), getSources()]);
  const ratio =
    overview.open_market_sells_30d > 0 ? overview.open_market_buys_30d / overview.open_market_sells_30d : null;
  const live = sources.filter((s) => s.records > 0);

  return (
    <div className="mt-10 grid gap-6">
      <section className="grid gap-4 lg:grid-cols-[1.3fr_2fr]">
        <div className="rounded-xl border border-hairline bg-surface px-6 py-5">
          <p className="text-sm text-ink-2">Bought on the open market by insiders, last 30 days</p>
          <p className="mt-2 text-5xl font-semibold tracking-tight">{formatEur(overview.buy_eur_30d)}</p>
          <p className="mt-2 text-sm text-ink-3">
            against {formatEur(overview.sell_eur_30d)} sold · last filing ingested{" "}
            <TimeAgo iso={overview.last_ingested_at} /> · data snapshot{" "}
            <code className="font-mono text-xs">{overview.snapshot}</code>
          </p>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <StatTile label="Filings" value={formatInt(overview.transactions)} note={`${overview.sources} live source`} />
          <StatTile label="Issuers" value={formatInt(overview.issuers)} note={`${overview.countries} country`} />
          <StatTile
            label="Buys per sell, 30 days"
            value={ratio == null ? "–" : `${ratio.toFixed(1)} : 1`}
            note={`${formatInt(overview.open_market_buys_30d)} buys · ${formatInt(overview.open_market_sells_30d)} sells`}
          />
          <StatTile
            label="Filed more than 5 days late"
            value={formatPercent(overview.late_share_365d)}
            note="Of filings in the last 12 months (MAR allows 3 business days)"
          />
        </div>
      </section>

      <Card
        title="Insider flow, weekly"
        subtitle="Open-market share purchases and sales by managers over the last six months, in euros at ECB reference rates."
      >
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
        <Card title="Latest filings" subtitle="Newest publications across all sources">
          <LiveTape initial={overview.latest} />
        </Card>
        <Card title="Largest open-market buys" subtitle="Last 30 days, in euros">
          <ol className="divide-y divide-hairline">
            {overview.largest_buys_30d.map((t, index) => (
              <li key={t.id} className="grid grid-cols-[1.5rem_1fr_auto] items-baseline gap-3 py-2.5 text-sm">
                <span className="tabular text-ink-3">{index + 1}</span>
                <div className="min-w-0">
                  {t.issuer_lei ? (
                    <Link href={`/company/${t.issuer_lei}`} className="block truncate font-medium hover:underline">
                      {t.issuer_name}
                    </Link>
                  ) : (
                    <span className="block truncate font-medium">{t.issuer_name}</span>
                  )}
                  <span className="text-xs text-ink-3">
                    {roleLabel(t.role)}
                    {t.notifier_name ? ` via ${t.notifier_name}` : ""} · traded {formatDate(t.transaction_date)}
                  </span>
                </div>
                <span className="tabular font-medium">{formatEur(t.amount_eur)}</span>
              </li>
            ))}
          </ol>
        </Card>
      </div>

      <Card title="Coverage" subtitle="One connector per regulator and dataset. More are being added in parallel.">
        <div className="overflow-x-auto">
          <table className="tabular w-full text-sm">
            <thead className="text-left text-ink-3">
              <tr className="border-b border-hairline">
                <th className="py-2 pr-4 font-medium">Country</th>
                <th className="py-2 pr-4 font-medium">Regulator</th>
                <th className="py-2 pr-4 font-medium">Status</th>
                <th className="py-2 pr-4 text-right font-medium">Records</th>
                <th className="py-2 text-right font-medium">Last publication</th>
              </tr>
            </thead>
            <tbody>
              {live.map((s) => (
                <tr key={s.name} className="border-b border-hairline/60">
                  <td className="py-2 pr-4">{countryName(s.country)}</td>
                  <td className="py-2 pr-4 text-ink-2">{s.authority}</td>
                  <td className="py-2 pr-4">
                    <span className="inline-flex items-center gap-1.5">
                      <span aria-hidden="true" className="size-2 rounded-full bg-live" /> Live
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-right">{formatInt(s.records)}</td>
                  <td className="py-2 text-right text-ink-2">
                    <TimeAgo iso={s.last_published_at} />
                  </td>
                </tr>
              ))}
              {PLANNED.map((p) => (
                <tr key={p.country} className="border-b border-hairline/60 text-ink-3">
                  <td className="py-2 pr-4">{countryName(p.country)}</td>
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
