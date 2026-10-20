import { getTranslations } from "next-intl/server";

import { Link } from "@/i18n/navigation";

export default async function NotFound() {
  const t = await getTranslations("errors");
  return (
    <div className="mx-auto max-w-lg py-24 text-center">
      <h1 className="text-2xl font-semibold tracking-tight">{t("notFoundTitle")}</h1>
      <p className="mt-3 text-ink-2">{t("notFoundBody")}</p>
      <Link href="/filings" className="mt-6 inline-block rounded-md bg-ink px-4 py-2 text-sm font-medium text-page hover:opacity-90">
        {t("searchFilings")}
      </Link>
    </div>
  );
}
