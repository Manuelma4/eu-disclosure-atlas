import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { TransactionsTable } from "@/components/transactions-table";
import { Card, Skeleton } from "@/components/ui";
import { getTransactions, type TransactionQuery } from "@/lib/api/data";

export const metadata: Metadata = {
  title: "Filings",
  description: "Search every managers' transaction in the Atlas by issuer, role, nature and date.",
};

const NATURES = ["buy", "sell", "subscription", "grant", "exercise", "conversion", "exchange", "gift"];
const ROLES = [
  ["ceo", "CEO"],
  ["cfo", "CFO"],
  ["chair", "Chair"],
  ["executive", "Executive"],
  ["board_member", "Board member"],
  ["other", "Other"],
];

type Search = Record<string, string | string[] | undefined>;
const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value) || undefined;

export default function FilingsPage({ searchParams }: PageProps<"/filings">) {
  return (
    <div className="grid gap-6">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">Filings</h1>
        <p className="mt-2 text-ink-2">
          Every managers&rsquo; transaction in the Atlas, newest first. The same query runs on the public API.
        </p>
      </header>
      <Suspense fallback={<Skeleton className="h-[36rem]" />}>
        <Results searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Results({ searchParams }: { searchParams: Promise<Search> }) {
  const params = await searchParams;
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
        <form className="flex flex-wrap items-end gap-3 text-sm" action="/filings">
          <label className="grid gap-1 font-normal text-ink-3">
            Issuer
            <input
              name="q"
              defaultValue={query.q ?? ""}
              placeholder="Name contains…"
              className="w-56 rounded-md border border-hairline bg-page px-2.5 py-1.5 text-ink placeholder:text-ink-3"
            />
          </label>
          <label className="grid gap-1 font-normal text-ink-3">
            Nature
            <select name="nature" defaultValue={query.nature?.[0] ?? ""} className="rounded-md border border-hairline bg-page px-2 py-1.5 text-ink">
              <option value="">All</option>
              {NATURES.map((n) => (
                <option key={n} value={n}>
                  {n.charAt(0).toUpperCase() + n.slice(1)}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1 font-normal text-ink-3">
            Role
            <select name="role" defaultValue={query.role?.[0] ?? ""} className="rounded-md border border-hairline bg-page px-2 py-1.5 text-ink">
              <option value="">All</option>
              {ROLES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1 font-normal text-ink-3">
            Published since
            <input type="date" name="since" defaultValue={query.since ?? ""} className="rounded-md border border-hairline bg-page px-2 py-1 text-ink" />
          </label>
          <label className="flex items-center gap-2 pb-1.5 font-normal text-ink-2">
            <input type="checkbox" name="open_market" value="true" defaultChecked={query.open_market === true} />
            Open market only
          </label>
          <button className="rounded-md bg-ink px-3 py-1.5 font-medium text-page hover:opacity-90" type="submit">
            Apply
          </button>
          {Object.keys(params).length > 0 && (
            <Link href="/filings" className="pb-1.5 text-ink-3 hover:text-ink">
              Reset
            </Link>
          )}
        </form>
      }
    >
      <TransactionsTable items={page.items} />
      <div className="mt-4 flex items-center justify-between text-sm text-ink-3">
        <span>
          Snapshot <code className="font-mono text-xs">{page.snapshot}</code>
        </span>
        {page.next_cursor && (
          <Link href={`/filings?${next.toString()}`} className="rounded-md border border-hairline px-3 py-1.5 text-ink hover:bg-surface-2">
            Next 50 →
          </Link>
        )}
      </div>
    </Card>
  );
}
