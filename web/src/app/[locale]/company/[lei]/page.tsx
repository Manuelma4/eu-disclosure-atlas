import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { DivergingBars } from "@/components/diverging-bars";
import { PriceChart } from "@/components/price-chart";
import { TransactionsTable } from "@/components/transactions-table";
import { Card, Skeleton, StatTile } from "@/components/ui";
import { Link } from "@/i18n/navigation";
import { getIssuer, getPrices } from "@/lib/api/data";
import { getFormat, getLabels } from "@/lib/format-server";
import { alternates } from "@/lib/seo";

export async function generateMetadata({ params }: PageProps<"/[locale]/company/[lei]">): Promise<Metadata> {
  const { lei } = await params;
  const [issuer, t, format] = await Promise.all([getIssuer(lei.toUpperCase()), getTranslations("company"), getFormat()]);
  if (!issuer) return { title: t("notFound") };
  return {
    title: t("metaTitle", { name: issuer.name }),
    description: t("metaDescription", { count: format.int(issuer.transactions), name: issuer.name, lei: issuer.lei }),
    alternates: await alternates(`/company/${issuer.lei}`),
  };
}

export default function CompanyPage({ params }: PageProps<"/[locale]/company/[lei]">) {
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
  const [t, nav, tt, format, labels] = await Promise.all([
    getTranslations("company"),
    getTranslations("nav"),
    getTranslations("table"),
    getFormat(),
    getLabels(),
  ]);

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
      <nav aria-label={t("breadcrumb")} className="text-sm text-ink-3">
        <Link href="/" className="hover:text-ink">
          {nav("overview")}
        </Link>{" "}
        / <span className="text-ink-2" aria-current="page">{issuer.name}</span>
      </nav>
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">{issuer.name}</h1>
        <p className="mt-2 text-sm text-ink-3">
          LEI <code className="font-mono text-xs text-ink-2">{issuer.lei}</code> · {format.country(issuer.country)} ·{" "}
          {t("lastFiling", { date: format.date(issuer.last_published_at) })}
        </p>
      </header>

      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label={t("net365")} value={format.signedEur(issuer.net_open_market_eur_365d)} note={t("net365Note")} />
        <StatTile label={t("buyers90")} value={format.int(issuer.distinct_buyers_90d)} note={t("people")} />
        <StatTile label={t("sellers90")} value={format.int(issuer.distinct_sellers_90d)} note={t("people")} />
        <StatTile label={t("filings")} value={format.int(issuer.transactions)} note={t("allTypes")} />
      </section>

      <Card title={t("priceTitle")} subtitle={t("priceSubtitle")}>
        <Suspense fallback={<Skeleton className="h-[340px]" />}>
          <Prices lei={issuer.lei} name={issuer.name} />
        </Suspense>
      </Card>

      <Card title={t("monthlyTitle")} subtitle={t("monthlySubtitle")}>
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
          <p className="text-sm text-ink-3">{t("noDated")}</p>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-[1fr_2.5fr]">
        <Card title={t("whoTitle")} subtitle={t("whoSubtitle")}>
          <table className="tabular w-full text-sm">
            <thead className="text-left text-ink-3">
              <tr className="border-b border-hairline">
                <th scope="col" className="py-2 pr-4 font-medium">{tt("role")}</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">{t("filings")}</th>
                <th scope="col" className="py-2 text-right font-medium">{t("people_col")}</th>
              </tr>
            </thead>
            <tbody>
              {issuer.roles.map((r) => (
                <tr key={r.role} className="border-b border-hairline/60">
                  <td className="py-2 pr-4">{labels.role(r.role)}</td>
                  <td className="py-2 pr-4 text-right">{format.int(r.transactions)}</td>
                  <td className="py-2 text-right">{format.int(r.people)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title={t("recentTitle")} subtitle={t("recentSubtitle")}>
          <TransactionsTable items={issuer.recent} showIssuer={false} />
        </Card>
      </div>
    </div>
  );
}

async function Prices({ lei, name }: { lei: string; name: string }) {
  const t = await getTranslations("price");
  const series = await getPrices(lei).catch(() => undefined);
  if (series === undefined) return <p className="text-sm text-ink-3">{t("unavailable")}</p>;
  if (series === null) return <p className="text-sm text-ink-3">{t("noPrices")}</p>;
  return <PriceChart series={series} issuerName={name} />;
}
