import Link from "next/link";

import type { Transaction } from "@/lib/api/data";
import { countryName, formatDate, formatEur, formatInt, roleLabel } from "@/lib/format";

import { NatureBadge } from "./ui";

export function TransactionsTable({ items, showIssuer = true }: { items: Transaction[]; showIssuer?: boolean }) {
  if (!items.length) return <p className="py-6 text-sm text-ink-3">No filings match.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="tabular w-full min-w-[720px] text-sm">
        <thead className="text-left text-ink-3">
          <tr className="border-b border-hairline">
            <th className="py-2 pr-4 font-medium">Traded</th>
            <th className="py-2 pr-4 font-medium">Published</th>
            {showIssuer && <th className="py-2 pr-4 font-medium">Issuer</th>}
            <th className="py-2 pr-4 font-medium">Role</th>
            <th className="py-2 pr-4 font-medium">Nature</th>
            <th className="py-2 pr-4 font-medium">Instrument</th>
            <th className="py-2 pr-4 text-right font-medium">Volume</th>
            <th className="py-2 text-right font-medium">Amount</th>
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <tr key={t.id} className="border-b border-hairline/60 align-baseline">
              <td className="py-2 pr-4 text-ink-2">{formatDate(t.transaction_date)}</td>
              <td className="py-2 pr-4 text-ink-2">
                {formatDate(t.published_at)}
                {t.filing_lag_days != null && t.filing_lag_days > 5 && (
                  <span className="ml-1.5 rounded border border-hairline px-1 text-[11px] text-ink-3" title="Published more than 5 calendar days after the trade">
                    late · {t.filing_lag_days} d
                  </span>
                )}
              </td>
              {showIssuer && (
                <td className="max-w-[16rem] py-2 pr-4">
                  {t.issuer_lei ? (
                    <Link href={`/company/${t.issuer_lei}`} className="block truncate font-medium text-ink hover:underline">
                      {t.issuer_name}
                    </Link>
                  ) : (
                    <span className="block truncate">{t.issuer_name}</span>
                  )}
                  <span className="text-xs text-ink-3">{countryName(t.country)}</span>
                </td>
              )}
              <td className="py-2 pr-4 text-ink-2">
                {roleLabel(t.role)}
                {t.notifier_name && <span className="block text-xs text-ink-3">via {t.notifier_name}</span>}
              </td>
              <td className="py-2 pr-4">
                <NatureBadge nature={t.nature} direction={t.direction} />
              </td>
              <td className="py-2 pr-4 text-ink-2">{t.instrument_type}</td>
              <td className="py-2 pr-4 text-right text-ink-2">{formatInt(t.volume)}</td>
              <td className="py-2 text-right font-medium text-ink">
                {formatEur(t.amount_eur)}
                {t.currency && t.currency !== "EUR" && t.amount != null && (
                  <span className="block text-xs font-normal text-ink-3">
                    {formatInt(Math.round(t.amount))} {t.currency}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
