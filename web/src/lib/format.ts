/**
 * Locale-aware formatting of numbers, dates, countries and relative times. Formatters are built once per
 * locale. Server components get them with `getFormat()` (lib/format-server.ts), client components with
 * `useFormat()` (lib/use-format.ts). Dates are shown in UTC, like the regulators' publication times.
 */
import { INTL_LOCALE, type Locale } from "@/i18n/routing";

export type Format = ReturnType<typeof build>;

const cache = new Map<string, Format>();

export function createFormat(locale: string): Format {
  let format = cache.get(locale);
  if (!format) {
    format = build(locale);
    cache.set(locale, format);
  }
  return format;
}

function build(locale: string) {
  const tag = INTL_LOCALE[locale as Locale] ?? locale;
  const eurCompact = new Intl.NumberFormat(tag, {
    style: "currency",
    currency: "EUR",
    notation: "compact",
    maximumFractionDigits: 1,
  });
  const integer = new Intl.NumberFormat(tag);
  const decimal = new Intl.NumberFormat(tag, { maximumFractionDigits: 2 });
  const oneDecimal = new Intl.NumberFormat(tag, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const percent = new Intl.NumberFormat(tag, { style: "percent", maximumFractionDigits: 1 });
  const bytes = new Intl.NumberFormat(tag, { style: "unit", unit: "megabyte", maximumFractionDigits: 1 });
  const dateUtc = new Intl.DateTimeFormat(tag, { dateStyle: "medium", timeZone: "UTC" });
  const dateTimeUtc = new Intl.DateTimeFormat(tag, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });
  const monthUtc = new Intl.DateTimeFormat(tag, { month: "short", year: "2-digit", timeZone: "UTC" });
  const dayMonthUtc = new Intl.DateTimeFormat(tag, { day: "numeric", month: "short", timeZone: "UTC" });
  const regions = new Intl.DisplayNames([tag], { type: "region" });
  const relative = new Intl.RelativeTimeFormat(tag, { numeric: "auto", style: "short" });

  const eur = (value: number | null | undefined) => (value == null ? "–" : eurCompact.format(value));
  return {
    locale: tag,
    eur,
    signedEur: (value: number | null | undefined) =>
      value == null ? "–" : `${value > 0 ? "+" : value < 0 ? "−" : ""}${eurCompact.format(Math.abs(value))}`,
    /** Axis labels: compact euros with a typographic minus. */
    eurAxis: (value: number) => (value === 0 ? eurCompact.format(0) : `${value < 0 ? "−" : ""}${eurCompact.format(Math.abs(value))}`),
    int: (value: number | null | undefined) => (value == null ? "–" : integer.format(value)),
    decimal: (value: number | null | undefined) => (value == null ? "–" : decimal.format(value)),
    oneDecimal: (value: number) => oneDecimal.format(value),
    percent: (value: number | null | undefined) => (value == null ? "–" : percent.format(value)),
    megabytes: (value: number) => bytes.format(value / 1_000_000),
    date: (iso: string | null | undefined) => (iso ? dateUtc.format(new Date(iso)) : "–"),
    dateTime: (iso: string | null | undefined) => (iso ? `${dateTimeUtc.format(new Date(iso))} UTC` : "–"),
    month: (iso: string) => monthUtc.format(new Date(iso)),
    dayMonth: (iso: string) => dayMonthUtc.format(new Date(iso)),
    country: (code: string) => regions.of(code.toUpperCase()) ?? code,
    /** "5 min ago", "3 h ago", "2 d ago"; null for under 45 seconds (callers show "just now"). */
    relative(iso: string, now: number): string | null {
      const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
      if (seconds < 45) return null;
      const minutes = Math.round(seconds / 60);
      if (minutes < 60) return relative.format(-minutes, "minute");
      const hours = Math.round(minutes / 60);
      if (hours < 36) return relative.format(-hours, "hour");
      return relative.format(-Math.round(hours / 24), "day");
    },
  };
}
