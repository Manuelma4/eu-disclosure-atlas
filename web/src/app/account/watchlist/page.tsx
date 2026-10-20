import type { Metadata } from "next";

import { WatchlistManager } from "@/components/account/watchlist-manager";

export const metadata: Metadata = {
  title: "Watchlist and alerts",
  description: "Follow issuers and get an email or a signed webhook when their insiders trade.",
  robots: { index: false },
};

export default function WatchlistPage() {
  return (
    <div className="grid gap-6">
      <header className="max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight">Watchlist and alerts</h1>
        <p className="mt-2 text-ink-2">
          Follow issuers by LEI. When a matching filing is ingested, the alerts service sends an email or a webhook
          signed with the Standard Webhooks scheme, retried after 5 minutes and 1 hour.
        </p>
      </header>
      <WatchlistManager />
    </div>
  );
}
