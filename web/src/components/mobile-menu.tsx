"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

/**
 * Navigation below the md breakpoint: a menu button that discloses a panel with the search, the sections, the
 * account entry and the language select. Escape or a click outside closes it and returns focus to the button;
 * following a link closes it.
 */
export function MobileMenu({ children }: { children: ReactNode }) {
  const t = useTranslations("nav");
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    // Move focus into the panel, on its first control.
    panel.current?.querySelector<HTMLElement>("a[href], button, select, input")?.focus();
    const close = (restoreFocus: boolean) => {
      setOpen(false);
      if (restoreFocus) button.current?.focus();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close(true);
      }
    };
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!panel.current?.contains(target) && !button.current?.contains(target)) close(false);
    };
    // Opening the search palette (a modal dialog) replaces the menu.
    const onSearch = () => close(false);
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    window.addEventListener("atlas:open-search", onSearch);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("atlas:open-search", onSearch);
    };
  }, [open]);

  return (
    <div className="md:hidden">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="flex size-11 items-center justify-center rounded-md text-ink-2 hover:bg-surface-2 hover:text-ink"
      >
        <span className="sr-only">{open ? t("closeMenu") : t("menu")}</span>
        <svg aria-hidden="true" viewBox="0 0 20 20" className="size-5">
          {open ? (
            <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          ) : (
            <path d="M3 6h14M3 10h14M3 14h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          )}
        </svg>
      </button>
      <div
        ref={panel}
        id={panelId}
        hidden={!open}
        onClick={(event) => (event.target as HTMLElement).closest("a[href]") && setOpen(false)}
        className="absolute inset-x-0 top-full max-h-[calc(100svh-3.5rem)] overflow-y-auto border-b border-hairline bg-surface px-4 pb-5 pt-3 shadow-lg"
      >
        {children}
      </div>
    </div>
  );
}

