import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { WatchlistManager } from "@/components/account/watchlist-manager";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("account");
  return { title: t("watchlistTitle"), description: t("watchlistDescription"), robots: { index: false } };
}

export default async function WatchlistPage() {
  const t = await getTranslations("account");
  return (
    <div className="grid gap-6">
      <header className="max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight">{t("watchlistTitle")}</h1>
        <p className="mt-2 text-ink-2">{t("watchlistLead")}</p>
      </header>
      <WatchlistManager />
    </div>
  );
}
