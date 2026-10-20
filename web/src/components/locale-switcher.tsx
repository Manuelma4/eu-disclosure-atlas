"use client";

import { useLocale, useTranslations } from "next-intl";
import { Suspense, useTransition } from "react";

import { usePathname, useRouter } from "@/i18n/navigation";
import { LOCALE_NAMES, routing, type Locale } from "@/i18n/routing";

type Variant = "bar" | "menu";

export function LocaleSwitcher({ variant = "bar" }: { variant?: Variant }) {
  return (
    <Suspense fallback={<Switcher pathname={null} variant={variant} />}>
      <LiveSwitcher variant={variant} />
    </Suspense>
  );
}

function LiveSwitcher({ variant }: { variant: Variant }) {
  return <Switcher pathname={usePathname()} variant={variant} />;
}

/** Language select; disabled until the current path is known (it streams in on dynamic routes). */
function Switcher({ pathname, variant }: { pathname: string | null; variant: Variant }) {
  const t = useTranslations("nav");
  const locale = useLocale();
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  return (
    <label className={`relative flex items-center ${variant === "menu" ? "justify-between gap-3 px-3" : ""}`}>
      <span className={variant === "menu" ? "text-ink-3" : "sr-only"}>{t("language")}</span>
      <select
        value={locale}
        disabled={pending || pathname === null}
        onChange={(event) => {
          const next = event.target.value as Locale;
          startTransition(() => router.replace(`${pathname ?? "/"}${window.location.search}`, { locale: next }));
        }}
        className={`cursor-pointer appearance-none rounded-md border border-hairline bg-surface py-0 pl-2.5 pr-7 text-ink-2 hover:text-ink ${
          variant === "menu" ? "h-11 text-base" : "h-8 text-sm"
        }`}
      >
        {routing.locales.map((l) => (
          <option key={l} value={l} lang={l}>
            {l.toUpperCase()} · {LOCALE_NAMES[l]}
          </option>
        ))}
      </select>
      <svg aria-hidden="true" viewBox="0 0 16 16" className={`pointer-events-none absolute size-3.5 text-ink-3 ${variant === "menu" ? "right-5" : "right-2"}`}>
        <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    </label>
  );
}
