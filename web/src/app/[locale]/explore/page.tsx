import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { Explorer } from "@/components/explorer/explorer";
import { alternates } from "@/lib/seo";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("explore");
  return { title: t("title"), description: t("metaDescription"), alternates: await alternates("/explore") };
}

export default async function ExplorePage() {
  const t = await getTranslations("explore");
  return (
    <div className="grid gap-6">
      <header className="max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-2 text-ink-2 text-pretty">{t("lead")}</p>
      </header>
      <Explorer />
    </div>
  );
}
