"use client";

import { useTranslations } from "next-intl";
import { useEffect } from "react";

export default function Error({ error, retry }: { error: unknown; retry: () => void }) {
  const t = useTranslations("errors");
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto max-w-lg py-24 text-center">
      <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
      <p className="mt-3 text-ink-2">{t("body")}</p>
      <button
        type="button"
        onClick={() => retry()}
        className="mt-6 rounded-md bg-ink px-4 py-2 text-sm font-medium text-page hover:opacity-90"
      >
        {t("retry")}
      </button>
    </div>
  );
}
