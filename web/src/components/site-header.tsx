import Link from "next/link";

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

export function SiteHeader() {
  return (
    <header className="border-b border-hairline bg-surface/80 backdrop-blur supports-[backdrop-filter]:bg-surface/70 sticky top-0 z-20">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight text-ink">
          <Logo />
          <span>EU Disclosure Atlas</span>
        </Link>
        <nav aria-label="Main" className="ml-auto flex items-center gap-1 text-sm text-ink-2">
          <Link className="rounded-md px-3 py-1.5 hover:bg-surface-2 hover:text-ink" href="/">
            Overview
          </Link>
          <Link className="rounded-md px-3 py-1.5 hover:bg-surface-2 hover:text-ink" href="/filings">
            Filings
          </Link>
          <Link className="rounded-md px-3 py-1.5 hover:bg-surface-2 hover:text-ink" href="/architecture">
            Architecture
          </Link>
          <a className="hidden rounded-md px-3 py-1.5 hover:bg-surface-2 hover:text-ink sm:block" href={API_DOCS}>
            API
          </a>
        </nav>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-hairline">
      <div className="mx-auto grid max-w-6xl gap-2 px-4 py-8 text-sm text-ink-3 sm:px-6">
        <p>
          A research and transparency project. Natural persons appear only as pseudonymous ids.{" "}
          <strong className="font-medium text-ink-2">Nothing here is investment advice.</strong>
        </p>
        <p>
          Sources: Finansinspektionen (FI), data processed and re-used free of charge with attribution · ECB euro
          reference rates. Built by{" "}
          <a className="underline decoration-hairline underline-offset-2 hover:text-ink" href="https://manuelma4.github.io/">
            Manuel David Maya Rosero
          </a>
          .
        </p>
      </div>
    </footer>
  );
}
