"use client";

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { Link } from "@/i18n/navigation";
import type { Transaction } from "@/lib/api/data";
import { useFormat, useLabels } from "@/lib/use-format";

import { TimeAgo } from "./time-ago";
import { NatureBadge } from "./ui";

// The Go realtime-gateway when configured (Kafka fan-out), else the Python API's own stream: same events.
const STREAM_URL =
  process.env.NEXT_PUBLIC_ATLAS_STREAM_URL ?? process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://127.0.0.1:8000";
type Status = "connecting" | "live" | "reconnecting";

/** Latest filings, kept current by server-sent events (GET /v1/stream on the gateway or the API). */
export function LiveTape({ initial, limit = 12 }: { initial: Transaction[]; limit?: number }) {
  const t = useTranslations("tape");
  const tn = useTranslations("natures");
  const format = useFormat();
  const labels = useLabels();
  const [items, setItems] = useState(initial.slice(0, limit));
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<Status>("connecting");

  useEffect(() => {
    const newest = [...initial].sort((a, b) => (a.first_seen_at < b.first_seen_at ? 1 : -1))[0];
    const since = newest ? `?since=${encodeURIComponent(`${newest.first_seen_at}|${newest.id}`)}` : "";
    const source = new EventSource(`${STREAM_URL}/v1/stream${since}`);
    source.onopen = () => setStatus("live");
    source.onerror = () => setStatus("reconnecting");
    source.addEventListener("filing", (event) => {
      const filing = JSON.parse((event as MessageEvent<string>).data) as Transaction;
      setItems((current) =>
        [filing, ...current.filter((x) => x.id !== filing.id)]
          .sort((a, b) => ((a.published_at ?? "") < (b.published_at ?? "") ? 1 : -1))
          .slice(0, limit),
      );
      setFresh((current) => new Set(current).add(filing.id));
    });
    return () => source.close();
  }, [initial, limit]);

  return (
    <div>
      <p className="mb-2 flex items-center gap-2 text-xs text-ink-3" aria-live="polite" data-testid="tape-status">
        <span aria-hidden="true" className={`size-2 rounded-full ${status === "live" ? "bg-live" : "bg-baseline"}`} />
        {status === "live" ? t("live") : t("connecting")}
      </p>
      <ul className="divide-y divide-hairline">
        {items.map((row) => (
          <li key={row.id} className={`grid grid-cols-[5.5rem_1fr_auto] items-baseline gap-3 py-2.5 text-sm ${fresh.has(row.id) ? "arrive" : ""}`}>
            <TimeAgo iso={row.published_at} className="text-xs text-ink-3" />
            <div className="min-w-0">
              {row.issuer_lei ? (
                <Link href={`/company/${row.issuer_lei}`} className="block truncate font-medium text-ink hover:underline">
                  {row.issuer_name}
                </Link>
              ) : (
                <span className="block truncate font-medium text-ink">{row.issuer_name}</span>
              )}
              <span className="text-xs text-ink-3">
                {labels.role(row.role)} ·{" "}
                <NatureBadge
                  nature={row.nature}
                  label={labels.nature(row.nature)}
                  hint={
                    row.nature !== "buy" && row.nature !== "sell" && row.direction !== 0
                      ? tn(row.direction > 0 ? "increases" : "decreases")
                      : undefined
                  }
                />{" "}
                · {labels.instrument(row.instrument_type)}
              </span>
            </div>
            <span className="tabular text-right text-ink-2">{format.eur(row.amount_eur)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
