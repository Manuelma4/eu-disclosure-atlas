import { getTranslations } from "next-intl/server";

import { Link } from "@/i18n/navigation";

import { AccountMenu } from "./account/account-menu";
import { CommandPalette, SearchButton } from "./command-palette";
import { LocaleSwitcher } from "./locale-switcher";
import { MobileMenu } from "./mobile-menu";
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
  const sections = [
    { href: "/", label: t("overview") },
    { href: "/filings", label: t("filings") },
    { href: "/explore", label: t("explore") },
    { href: "/architecture", label: t("architecture") },
  ];
  return (
    <header className="sticky top-0 z-20 border-b border-hairline bg-surface/80 backdrop-blur supports-[backdrop-filter]:bg-surface/70">
      <div className="relative mx-auto flex h-14 max-w-6xl items-center gap-3 px-4 sm:px-6 lg:gap-5">
        <Link
          href="/"
          aria-label={t("home")}
          className="flex min-h-11 shrink-0 items-center gap-2 rounded-md font-semibold tracking-tight whitespace-nowrap text-ink"
        >
          <Logo />
          <span className="md:hidden xl:inline">EU Disclosure Atlas</span>
        </Link>

        {/* md and up: inline navigation */}
        <nav aria-label={t("main")} className="hidden min-w-0 items-center gap-0.5 text-sm text-ink-2 md:flex">
          {sections.map((s) => (
            <NavLink key={s.href} href={s.href}>
              {s.label}
            </NavLink>
          ))}
          <a className="hidden rounded-md px-3 py-1.5 whitespace-nowrap hover:bg-surface-2 hover:text-ink lg:block" href={API_DOCS}>
            {t("api")}
          </a>
        </nav>
        <div className="ml-auto hidden items-center gap-2 text-sm text-ink-2 md:flex">
          <SearchButton />
          <LocaleSwitcher />
          <AccountMenu />
        </div>

        {/* below md: everything collapses into the menu */}
        <div className="ml-auto md:hidden">
          <MobileMenu>
            <SearchButton variant="menu" />
            <nav aria-label={t("main")} className="mt-3 grid text-[15px] text-ink-2">
              {sections.map((s) => (
                <NavLink key={s.href} href={s.href} variant="menu">
                  {s.label}
                </NavLink>
              ))}
              <a className="flex min-h-11 items-center rounded-md px-3 hover:bg-surface-2 hover:text-ink" href={API_DOCS}>
                {t("api")}
              </a>
            </nav>
            <div className="mt-2 grid gap-3 border-t border-hairline pt-3 text-[15px] text-ink-2">
              <div className="grid">
                <AccountMenu variant="menu" />
              </div>
              <LocaleSwitcher variant="menu" />
            </div>
          </MobileMenu>
        </div>
      </div>
      <CommandPalette />
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
