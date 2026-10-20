const eurCompact = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "EUR",
  notation: "compact",
  maximumFractionDigits: 1,
});
const integer = new Intl.NumberFormat("en-GB");
const percent = new Intl.NumberFormat("en-GB", { style: "percent", maximumFractionDigits: 1 });
const dateUtc = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "UTC" });
const dateTimeUtc = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });
const monthUtc = new Intl.DateTimeFormat("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" });
const regions = new Intl.DisplayNames(["en"], { type: "region" });

export const formatEur = (value: number | null | undefined) => (value == null ? "–" : eurCompact.format(value));

export const formatSignedEur = (value: number | null | undefined) =>
  value == null ? "–" : `${value > 0 ? "+" : value < 0 ? "−" : ""}${eurCompact.format(Math.abs(value))}`;

export const formatInt = (value: number | null | undefined) => (value == null ? "–" : integer.format(value));

export const formatPercent = (value: number | null | undefined) => (value == null ? "–" : percent.format(value));

export const formatDate = (iso: string | null | undefined) => (iso ? dateUtc.format(new Date(iso)) : "–");

export const formatDateTime = (iso: string | null | undefined) =>
  iso ? `${dateTimeUtc.format(new Date(iso))} UTC` : "–";

export const formatMonth = (iso: string) => monthUtc.format(new Date(iso));

export const countryName = (code: string) => regions.of(code.toUpperCase()) ?? code;

export function relativeTime(iso: string, now: number): string {
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

const ROLE_LABELS: Record<string, string> = {
  ceo: "CEO",
  cfo: "CFO",
  chair: "Chair",
  executive: "Executive",
  board_member: "Board member",
  other: "Other",
  unknown: "Unknown",
};

export const roleLabel = (role: string) => ROLE_LABELS[role] ?? role;

export const natureLabel = (nature: string) =>
  nature === "internal_transfer" ? "Internal transfer" : nature.charAt(0).toUpperCase() + nature.slice(1);
