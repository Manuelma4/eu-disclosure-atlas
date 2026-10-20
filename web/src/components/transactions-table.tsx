import { getTranslations } from "next-intl/server";

import { Link } from "@/i18n/navigation";
import type { Transaction } from "@/lib/api/data";
import { getFormat, getLabels } from "@/lib/format-server";

import { NatureBadge } from "./ui";

export async function TransactionsTable({ items, showIssuer = true }: { items: Transaction[]; showIssuer?: boolean }) {
  const [t, tn, format, labels] = await Promise.all([
    getTranslations("table"),
    getTranslations("natures"),
    getFormat(),
    getLabels(),
  ]);
  if (!items.length) return <p className="py-6 text-sm text-ink-3">{t("empty")}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="tabular w-full min-w-[720px] text-sm">
        <thead className="text-left text-ink-3">
          <tr className="border-b border-hairline">
            <th scope="col" className="py-2 pr-4 font-medium">{t("traded")}</th>
            <th scope="col" className="py-2 pr-4 font-medium">{t("published")}</th>
            {showIssuer && <th scope="col" className="py-2 pr-4 font-medium">{t("issuer")}</th>}
            <th scope="col" className="py-2 pr-4 font-medium">{t("role")}</th>
            <th scope="col" className="py-2 pr-4 font-medium">{t("nature")}</th>
            <th scope="col" className="py-2 pr-4 font-medium">{t("instrument")}</th>
            <th scope="col" className="py-2 pr-4 text-right font-medium">{t("volume")}</th>
            <th scope="col" className="py-2 text-right font-medium">{t("amount")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => (
            <tr key={row.id} className="border-b border-hairline/60 align-baseline">
              <td className="py-2 pr-4 text-ink-2">{format.date(row.transaction_date)}</td>
              <td className="py-2 pr-4 text-ink-2">
                {format.date(row.published_at)}
                {row.filing_lag_days != null && row.filing_lag_days > 5 && (
                  <span className="ml-1.5 rounded border border-hairline px-1 text-[11px] text-ink-3" title={t("lateTitle")}>
                    {t("late", { days: row.filing_lag_days })}
                  </span>
                )}
              </td>
              {showIssuer && (
                <td className="max-w-[16rem] py-2 pr-4">
                  {row.issuer_lei ? (
                    <Link href={`/company/${row.issuer_lei}`} className="block truncate font-medium text-ink hover:underline">
                      {row.issuer_name}
                    </Link>
                  ) : (
                    <span className="block truncate">{row.issuer_name}</span>
                  )}
                  <span className="text-xs text-ink-3">{format.country(row.country)}</span>
                </td>
              )}
              <td className="py-2 pr-4 text-ink-2">
                {labels.role(row.role)}
                {row.notifier_name && <span className="block text-xs text-ink-3">{t("via", { name: row.notifier_name })}</span>}
              </td>
              <td className="py-2 pr-4">
                <NatureBadge
                  nature={row.nature}
                  label={labels.nature(row.nature)}
                  hint={directionHint(row, tn("increases"), tn("decreases"))}
                />
              </td>
              <td className="py-2 pr-4 text-ink-2">{labels.instrument(row.instrument_type)}</td>
              <td className="py-2 pr-4 text-right text-ink-2">{format.int(row.volume)}</td>
              <td className="py-2 text-right font-medium text-ink">
                {format.eur(row.amount_eur)}
                {row.currency && row.currency !== "EUR" && row.amount != null && (
                  <span className="block text-xs font-normal text-ink-3">
                    {format.int(Math.round(row.amount))} {row.currency}
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

export function directionHint(row: Pick<Transaction, "nature" | "direction">, up: string, down: string) {
  if (row.nature === "buy" || row.nature === "sell" || row.direction === 0) return undefined;
  return row.direction > 0 ? up : down;
}
