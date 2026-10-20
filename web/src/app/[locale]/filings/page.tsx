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
    <Card>
        <form
          role="search"
          aria-label={t("filters")}
          className="grid grid-cols-2 items-end gap-3 border-b border-hairline pb-4 text-sm sm:flex sm:flex-wrap"
          action=""
        >
          <label className="col-span-2 grid gap-1 font-normal text-ink-3 sm:col-span-1">
            {t("issuer")}
            <input
              name="q"
              type="search"
              defaultValue={query.q ?? ""}
              placeholder={t("issuerPlaceholder")}
              className={`w-full px-2.5 py-1.5 placeholder:text-ink-3 sm:w-56 ${field}`}
            />
          </label>
          <label className="grid gap-1 font-normal text-ink-3">
            {t("nature")}
            <select name="nature" defaultValue={query.nature?.[0] ?? ""} className={`w-full px-2 py-1.5 ${field}`}>
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
            <select name="role" defaultValue={query.role?.[0] ?? ""} className={`w-full px-2 py-1.5 ${field}`}>
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
            <input type="date" name="since" defaultValue={query.since ?? ""} className={`w-full px-2 py-1 ${field}`} />
          </label>
          <label className="flex min-h-11 items-center gap-2 font-normal text-ink-2 sm:min-h-0 sm:pb-1.5">
            <input type="checkbox" name="open_market" value="true" defaultChecked={query.open_market === true} />
            {t("openMarket")}
          </label>
          <div className="col-span-2 flex items-center gap-4 sm:col-span-1">
            <button className="rounded-md bg-ink px-4 py-1.5 font-medium text-page hover:opacity-90" type="submit">
              {t("apply")}
            </button>
            {Object.keys(params).length > 0 && (
              <Link href="/filings" className="flex min-h-11 items-center text-ink-3 hover:text-ink sm:min-h-0">
                {t("reset")}
              </Link>
            )}
          </div>
        </form>
      <TransactionsTable items={page.items} />
      <div className="mt-4 flex items-center justify-between text-sm text-ink-3">
        <span>
          {tc("snapshot")} <code className="font-mono text-xs">{page.snapshot}</code>
        </span>
        {page.next_cursor && (
          <Link
            href={`/filings?${next.toString()}`}
            className="flex min-h-11 items-center rounded-md border border-hairline px-3 text-ink hover:bg-surface-2 sm:min-h-0 sm:py-1.5"
          >
            {t("next")} <span aria-hidden="true">→</span>
          </Link>
        )}
      </div>
    </Card>
  );
}
