import { getTranslations } from "next-intl/server";

import { Link } from "@/i18n/navigation";

import { CommandPalette } from "./command-palette";
import { LocaleSwitcher } from "./locale-switcher";
import { NavLink } from "./nav-link";

const API_DOCS = `${process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://127.0.0.1:8000"}/docs`;

export function Logo() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className="size-7 shrink-0">
      <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="2" />
      <ellipse cx="16" cy="16" rx="6" ry="13" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M3.5 12h25M3.5 20h25" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="22" cy="10" r="3" fill="var(--accent)" />
    </svg>
  );
}

export async function SiteHeader() {
  const t = await getTranslations("nav");
  return (
    <header className="sticky top-0 z-20 border-b border-hairline bg-surface/80 backdrop-blur supports-[backdrop-filter]:bg-surface/70">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4 sm:gap-5 sm:px-6">
        <Link href="/" aria-label={t("home")} className="flex items-center gap-2 rounded-md font-semibold tracking-tight text-ink">
          <Logo />
          <span className="hidden md:inline">EU Disclosure Atlas</span>
        </Link>
        <nav aria-label={t("main")} className="flex min-w-0 items-center gap-0.5 overflow-x-auto text-sm text-ink-2">
          <NavLink href="/">{t("overview")}</NavLink>
          <NavLink href="/filings">{t("filings")}</NavLink>
          <NavLink href="/explore">{t("explore")}</NavLink>
          <NavLink href="/architecture" className="hidden sm:block">
            {t("architecture")}
          </NavLink>
          <a className="hidden rounded-md px-3 py-1.5 hover:bg-surface-2 hover:text-ink lg:block" href={API_DOCS}>
            {t("api")}
          </a>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <CommandPalette />
          <LocaleSwitcher />
        </div>
      </div>
    </header>
  );
}

export async function SiteFooter() {
  const t = await getTranslations("footer");
  return (
    <footer className="mt-16 border-t border-hairline">
      <div className="mx-auto grid max-w-6xl gap-2 px-4 py-8 text-sm text-ink-3 sm:px-6">
        <p>
          {t("privacy")} <strong className="font-medium text-ink-2">{t("advice")}</strong>
        </p>
        <p>
          {t("sources")}{" "}
          {t.rich("builtBy", {
            link: (chunks) => (
              <a className="underline decoration-hairline underline-offset-2 hover:text-ink" href="https://manuelma4.github.io/">
                {chunks}
              </a>
            ),
          })}
        </p>
      </div>
    </footer>
  );
}
