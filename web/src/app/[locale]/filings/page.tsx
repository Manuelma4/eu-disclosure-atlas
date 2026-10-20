import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { TransactionsTable } from "@/components/transactions-table";
import { Card, Skeleton } from "@/components/ui";
import { Link } from "@/i18n/navigation";
import { getTransactions, type TransactionQuery } from "@/lib/api/data";
import { getLabels } from "@/lib/format-server";
import { alternates } from "@/lib/seo";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("filings");
  return { title: t("title"), description: t("metaDescription"), alternates: await alternates("/filings") };
}

const NATURES = ["buy", "sell", "subscription", "grant", "exercise", "conversion", "exchange", "gift"];
const ROLES = ["ceo", "cfo", "chair", "executive", "board_member", "other"];

type Search = Record<string, string | string[] | undefined>;
const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value) || undefined;

export default async function FilingsPage({ searchParams }: PageProps<"/[locale]/filings">) {
  const t = await getTranslations("filings");
  return (
    <div className="grid gap-6">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-2 text-ink-2">{t("lead")}</p>
      </header>
      <Suspense fallback={<Skeleton className="h-[36rem]" />}>
        <Results searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

const field = "rounded-md border border-hairline bg-page text-ink";

async function Results({ searchParams }: { searchParams: Promise<Search> }) {
  const params = await searchParams;
  const [t, tc, labels] = await Promise.all([getTranslations("filings"), getTranslations("common"), getLabels()]);
  const query: TransactionQuery = {
    q: first(params.q),
    nature: first(params.nature) ? [first(params.nature)!] : undefined,
    role: first(params.role) ? [first(params.role)!] : undefined,
    open_market: first(params.open_market) === "true" ? true : undefined,
    since: first(params.since),
    cursor: first(params.cursor),
    limit: 50,
  };
  const page = await getTransactions(query);
  const next = new URLSearchParams(
    Object.entries({ ...params, cursor: page.next_cursor ?? "" }).flatMap(([k, v]) =>
      typeof v === "string" && v ? [[k, v]] : [],
    ),
  );

  return (
    <Card
      title={
        <form role="search" aria-label={t("filters")} className="flex flex-wrap items-end gap-3 text-sm" action="">
          <label className="grid gap-1 font-normal text-ink-3">
            {t("issuer")}
            <input
              name="q"
              type="search"
              defaultValue={query.q ?? ""}
              placeholder={t("issuerPlaceholder")}
              className={`w-56 px-2.5 py-1.5 placeholder:text-ink-3 ${field}`}
            />
          </label>
          <label className="grid gap-1 font-normal text-ink-3">
            {t("nature")}
            <select name="nature" defaultValue={query.nature?.[0] ?? ""} className={`px-2 py-1.5 ${field}`}>
              <option value="">{t("all")}</option>
              {NATURES.map((n) => (
                <option key={n} value={n}>
                  {labels.nature(n)}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1 font-normal text-ink-3">
            {t("role")}
            <select name="role" defaultValue={query.role?.[0] ?? ""} className={`px-2 py-1.5 ${field}`}>
              <option value="">{t("all")}</option>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {labels.role(r)}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1 font-normal text-ink-3">
            {t("since")}
            <input type="date" name="since" defaultValue={query.since ?? ""} className={`px-2 py-1 ${field}`} />
          </label>
          <label className="flex items-center gap-2 pb-1.5 font-normal text-ink-2">
            <input type="checkbox" name="open_market" value="true" defaultChecked={query.open_market === true} />
            {t("openMarket")}
          </label>
          <button className="rounded-md bg-ink px-3 py-1.5 font-medium text-page hover:opacity-90" type="submit">
            {t("apply")}
          </button>
          {Object.keys(params).length > 0 && (
            <Link href="/filings" className="pb-1.5 text-ink-3 hover:text-ink">
              {t("reset")}
            </Link>
          )}
        </form>
      }
    >
      <TransactionsTable items={page.items} />
      <div className="mt-4 flex items-center justify-between text-sm text-ink-3">
        <span>
          {tc("snapshot")} <code className="font-mono text-xs">{page.snapshot}</code>
        </span>
        {page.next_cursor && (
          <Link href={`/filings?${next.toString()}`} className="rounded-md border border-hairline px-3 py-1.5 text-ink hover:bg-surface-2">
            {t("next")} <span aria-hidden="true">→</span>
          </Link>
        )}
      </div>
    </Card>
  );
}
