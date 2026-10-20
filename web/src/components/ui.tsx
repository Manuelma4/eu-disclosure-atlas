import type { ReactNode } from "react";

export function Card({ title, subtitle, action, children, className = "" }: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`min-w-0 rounded-xl border border-hairline bg-surface ${className}`}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-4 px-5 pt-4">
          <div>
            {title && <h2 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-sm text-ink-3">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className="px-5 pb-5 pt-3">{children}</div>
    </section>
  );
}

export function StatTile({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="rounded-xl border border-hairline bg-surface px-5 py-4">
      <p className="text-sm text-ink-2">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight text-ink">{value}</p>
      {note && <p className="mt-1 text-xs text-ink-3">{note}</p>}
    </div>
  );
}

/** Nature of a transaction with its buy/sell color. `label` and `hint` (screen readers) come translated. */
export function NatureBadge({ nature, label, hint }: { nature: string; label: string; hint?: string }) {
  const tone = nature === "buy" ? "var(--buy)" : nature === "sell" ? "var(--sell)" : "var(--baseline)";
  return (
    <span className="inline-flex items-center gap-1.5 text-ink-2">
      <span aria-hidden="true" className="size-2 rounded-[2px]" style={{ background: tone }} />
      {label}
      {hint && <span className="sr-only">{hint}</span>}
    </span>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden="true" className={`animate-pulse rounded-xl border border-hairline bg-surface ${className}`} />;
}

/** Horizontal scroll area for a wide table: keyboard-focusable and labelled, as WCAG requires of scroll regions. */
export function TableScroll({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div role="region" aria-label={label} tabIndex={0} className={`overflow-x-auto rounded-sm ${className}`}>
      {children}
    </div>
  );
}
