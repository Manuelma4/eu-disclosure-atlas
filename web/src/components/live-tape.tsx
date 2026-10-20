"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import type { Transaction } from "@/lib/api/data";
import { formatEur, roleLabel } from "@/lib/format";

import { TimeAgo } from "./time-ago";
import { NatureBadge } from "./ui";

// The Go realtime-gateway when configured (Kafka fan-out), else the Python API's own stream: same events.
const STREAM_URL =
  process.env.NEXT_PUBLIC_ATLAS_STREAM_URL ?? process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://127.0.0.1:8000";
type Status = "connecting" | "live" | "reconnecting";

/** Latest filings, kept current by server-sent events (GET /v1/stream on the gateway or the API). */
export function LiveTape({ initial, limit = 12 }: { initial: Transaction[]; limit?: number }) {
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
        [filing, ...current.filter((t) => t.id !== filing.id)]
          .sort((a, b) => ((a.published_at ?? "") < (b.published_at ?? "") ? 1 : -1))
          .slice(0, limit),
      );
      setFresh((current) => new Set(current).add(filing.id));
    });
    return () => source.close();
  }, [initial, limit]);

  return (
    <div>
      <p className="mb-2 flex items-center gap-2 text-xs text-ink-3" aria-live="polite">
        <span
          aria-hidden="true"
          className={`size-2 rounded-full ${status === "live" ? "bg-live" : "bg-baseline"}`}
        />
        {status === "live" ? "Live: new filings appear here as they are ingested" : "Connecting to the live stream…"}
      </p>
      <ul className="divide-y divide-hairline">
        {items.map((t) => (
          <li key={t.id} className={`grid grid-cols-[5.5rem_1fr_auto] items-baseline gap-3 py-2.5 text-sm ${fresh.has(t.id) ? "arrive" : ""}`}>
            <TimeAgo iso={t.published_at} className="text-xs text-ink-3" />
            <div className="min-w-0">
              {t.issuer_lei ? (
                <Link href={`/company/${t.issuer_lei}`} className="block truncate font-medium text-ink hover:underline">
                  {t.issuer_name}
                </Link>
              ) : (
                <span className="block truncate font-medium text-ink">{t.issuer_name}</span>
              )}
              <span className="text-xs text-ink-3">
                {roleLabel(t.role)} · <NatureBadge nature={t.nature} direction={t.direction} /> ·{" "}
                {t.instrument_type}
              </span>
            </div>
            <span className="tabular text-right text-ink-2">{formatEur(t.amount_eur)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
