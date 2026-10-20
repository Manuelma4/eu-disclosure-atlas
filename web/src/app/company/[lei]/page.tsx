import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { DivergingBars } from "@/components/diverging-bars";
import { TransactionsTable } from "@/components/transactions-table";
import { Card, Skeleton, StatTile } from "@/components/ui";
import { getIssuer } from "@/lib/api/data";
import { countryName, formatDate, formatInt, formatSignedEur, roleLabel } from "@/lib/format";

export async function generateMetadata({ params }: PageProps<"/company/[lei]">): Promise<Metadata> {
  const { lei } = await params;
  const issuer = await getIssuer(lei.toUpperCase());
  if (!issuer) return { title: "Issuer not found" };
  return {
    title: `${issuer.name}: insider trades`,
    description: `${formatInt(issuer.transactions)} managers' transactions disclosed for ${issuer.name} (LEI ${issuer.lei}), with monthly insider buying and selling.`,
  };
}

export default function CompanyPage({ params }: PageProps<"/company/[lei]">) {
  return (
    <Suspense fallback={<Skeleton className="h-[32rem]" />}>
      <Company params={params} />
    </Suspense>
  );
}

async function Company({ params }: { params: Promise<{ lei: string }> }) {
  const { lei } = await params;
  const issuer = await getIssuer(lei.toUpperCase());
  if (!issuer) notFound();

  return (
    <div className="grid gap-6">
      <nav aria-label="Breadcrumb" className="text-sm text-ink-3">
        <Link href="/" className="hover:text-ink">
          Overview
        </Link>{" "}
        / <span className="text-ink-2">{issuer.name}</span>
      </nav>
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">{issuer.name}</h1>
        <p className="mt-2 text-sm text-ink-3">
          LEI <code className="font-mono text-xs text-ink-2">{issuer.lei}</code> · {countryName(issuer.country)} · last
          filing published {formatDate(issuer.last_published_at)}
        </p>
      </header>

      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile
          label="Net open-market trading, 12 months"
          value={formatSignedEur(issuer.net_open_market_eur_365d)}
          note="Insider buys minus sells, in euros"
        />
        <StatTile label="Insiders buying, 90 days" value={formatInt(issuer.distinct_buyers_90d)} note="Distinct people" />
        <StatTile label="Insiders selling, 90 days" value={formatInt(issuer.distinct_sellers_90d)} note="Distinct people" />
        <StatTile label="Filings" value={formatInt(issuer.transactions)} note="All transaction types" />
      </section>

      <Card title="Insider flow, monthly" subtitle="Open-market share purchases and sales by managers, in euros.">
        {issuer.monthly.length ? (
          <DivergingBars
            granularity="month"
            data={issuer.monthly.map((m) => ({
              period: m.month,
              buy: m.buy_eur ?? 0,
              sell: m.sell_eur ?? 0,
              buys: m.buys,
              sells: m.sells,
            }))}
          />
        ) : (
          <p className="text-sm text-ink-3">No dated transactions yet.</p>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-[1fr_2.5fr]">
        <Card title="Who files" subtitle="By most senior role">
          <table className="tabular w-full text-sm">
            <thead className="text-left text-ink-3">
              <tr className="border-b border-hairline">
                <th className="py-2 pr-4 font-medium">Role</th>
                <th className="py-2 pr-4 text-right font-medium">Filings</th>
                <th className="py-2 text-right font-medium">People</th>
              </tr>
            </thead>
            <tbody>
              {issuer.roles.map((r) => (
                <tr key={r.role} className="border-b border-hairline/60">
                  <td className="py-2 pr-4">{roleLabel(r.role)}</td>
                  <td className="py-2 pr-4 text-right">{formatInt(r.transactions)}</td>
                  <td className="py-2 text-right">{formatInt(r.people)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Filings" subtitle="Most recent 100; people appear as pseudonyms, legal entities by name">
          <TransactionsTable items={issuer.recent} showIssuer={false} />
        </Card>
      </div>
    </div>
  );
}
