"use client";

import { useTranslations } from "next-intl";
import { useSyncExternalStore } from "react";

import { useFormat } from "@/lib/use-format";

const TICK = 30_000;
const subscribe = (onTick: () => void) => {
  const timer = setInterval(onTick, TICK);
  return () => clearInterval(timer);
};
// Stable within each 30-second window, as useSyncExternalStore requires.
const clientNow = () => Math.floor(Date.now() / TICK) * TICK;
const serverNow = () => null;

/** Absolute UTC time on the server (deterministic), relative time once hydrated in the browser. */
export function TimeAgo({ iso, className = "" }: { iso: string | null | undefined; className?: string }) {
  const now = useSyncExternalStore(subscribe, clientNow, serverNow);
  const format = useFormat();
  const t = useTranslations("time");
  if (!iso) return <span className={className}>–</span>;
  return (
    <time dateTime={iso} title={format.dateTime(iso)} className={className}>
      {now === null ? format.dateTime(iso) : (format.relative(iso, now) ?? t("justNow"))}
    </time>
  );
}
