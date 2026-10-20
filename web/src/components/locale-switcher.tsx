"use client";

import { useLocale, useTranslations } from "next-intl";
import { Suspense, useTransition } from "react";

import { usePathname, useRouter } from "@/i18n/navigation";
import { LOCALE_NAMES, routing, type Locale } from "@/i18n/routing";

export function LocaleSwitcher() {
  return (
    <Suspense fallback={<Switcher pathname={null} />}>
      <LiveSwitcher />
    </Suspense>
  );
}

function LiveSwitcher() {
  return <Switcher pathname={usePathname()} />;
}

/** Language select; disabled until the current path is known (it streams in on dynamic routes). */
function Switcher({ pathname }: { pathname: string | null }) {
  const t = useTranslations("nav");
  const locale = useLocale();
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  return (
    <label className="relative flex items-center">
      <span className="sr-only">{t("language")}</span>
      <select
        value={locale}
        disabled={pending || pathname === null}
        onChange={(event) => {
          const next = event.target.value as Locale;
          startTransition(() => router.replace(`${pathname ?? "/"}${window.location.search}`, { locale: next }));
        }}
        className="h-8 cursor-pointer appearance-none rounded-md border border-hairline bg-surface py-0 pl-2.5 pr-7 text-sm text-ink-2 hover:text-ink"
      >
        {routing.locales.map((l) => (
          <option key={l} value={l} lang={l}>
            {l.toUpperCase()} · {LOCALE_NAMES[l]}
          </option>
        ))}
      </select>
      <svg aria-hidden="true" viewBox="0 0 16 16" className="pointer-events-none absolute right-2 size-3.5 text-ink-3">
        <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    </label>
  );
}
