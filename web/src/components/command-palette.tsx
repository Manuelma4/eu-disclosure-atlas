"use client";

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore } from "react";

import { useRouter } from "@/i18n/navigation";
import type { IssuerSummary } from "@/lib/api/data";
import { useFormat } from "@/lib/use-format";

const API_URL = process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://127.0.0.1:8000";

type Item = { id: string; href: string; label: string; detail?: string; group: "companies" | "pages" };
type State = { status: "idle" | "loading" | "done" | "error"; issuers: IssuerSummary[] };

const isMac = () => /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const noSubscribe = () => () => {};

/** Ctrl/Cmd+K search over issuers (GET /v1/issuers?q=) and the site's pages, as an accessible combobox. */
export function CommandPalette() {
  const t = useTranslations("search");
  const nav = useTranslations("nav");
  const format = useFormat();
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [state, setState] = useState<State>({ status: "idle", issuers: [] });
  const mac = useSyncExternalStore(noSubscribe, isMac, () => false);

  const open = useCallback(() => {
    if (dialog.current?.open) return;
    dialog.current?.showModal();
    input.current?.select();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        open();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setState((s) => ({ ...s, status: "loading" }));
      try {
        const url = `${API_URL}/v1/issuers?${new URLSearchParams({ q, limit: "8" })}`;
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) throw new Error(String(response.status));
        setState({ status: "done", issuers: (await response.json()) as IssuerSummary[] });
        setActive(0);
      } catch {
        if (!controller.signal.aborted) setState({ status: "error", issuers: [] });
      }
    }, 150);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  const searching = query.trim().length >= 2;
  const pages: Item[] = [
    { id: "p-home", href: "/", label: nav("overview"), group: "pages" },
    { id: "p-filings", href: "/filings", label: nav("filings"), group: "pages" },
    { id: "p-explore", href: "/explore", label: nav("explore"), group: "pages" },
    { id: "p-architecture", href: "/architecture", label: nav("architecture"), group: "pages" },
  ].filter((p) => !searching || p.label.toLowerCase().includes(query.trim().toLowerCase())) as Item[];
  const companies: Item[] = searching
    ? state.issuers.map((i) => ({
        id: `c-${i.lei}`,
        href: `/company/${i.lei}`,
        label: i.name,
        detail: `${format.country(i.country)} · ${t("filings", { count: i.transactions })}`,
        group: "companies",
      }))
    : [];
  const items = [...companies, ...pages];
  const current = items[Math.min(active, items.length - 1)];

  const go = (item: Item | undefined) => {
    if (!item) return;
    dialog.current?.close();
    setQuery("");
    router.push(item.href);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((a) => (items.length ? (Math.min(a, items.length - 1) + step + items.length) % items.length : 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(current);
    }
  };

  const renderGroup = (group: Item["group"], list: Item[]) =>
    list.length > 0 && (
      <li role="presentation">
        <p id={`${listId}-${group}`} className="px-3 pb-1 pt-3 text-xs font-medium text-ink-3">
          {t(group)}
        </p>
        <ul role="group" aria-labelledby={`${listId}-${group}`}>
          {list.map((item) => {
            const selected = item.id === current?.id;
            return (
              <li
                key={item.id}
                id={`${listId}-${item.id}`}
                role="option"
                aria-selected={selected}
                onPointerMove={() => setActive(items.indexOf(item))}
                onClick={() => go(item)}
                className={`mx-1.5 flex cursor-pointer items-baseline justify-between gap-3 rounded-md px-2.5 py-2 text-sm ${
                  selected ? "bg-surface-2 text-ink" : "text-ink-2"
                }`}
              >
                <span className="truncate font-medium">{item.label}</span>
                {item.detail && <span className="shrink-0 text-xs text-ink-3">{item.detail}</span>}
              </li>
            );
          })}
        </ul>
      </li>
    );

  return (
    <>
      <button
        type="button"
        onClick={open}
        aria-haspopup="dialog"
        aria-keyshortcuts="Control+K Meta+K"
        className="flex h-8 items-center gap-2 rounded-md border border-hairline bg-surface px-2.5 text-sm text-ink-3 hover:text-ink"
      >
        <svg aria-hidden="true" viewBox="0 0 16 16" className="size-3.5">
          <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.5" />
        </svg>
        <span className="hidden sm:inline">{t("open")}</span>
        <kbd className="hidden rounded border border-hairline px-1 font-sans text-[11px] text-ink-3 sm:inline">
          {t("shortcut", { key: mac ? "⌘" : "Ctrl" })}
        </kbd>
      </button>

      <dialog
        ref={dialog}
        aria-label={t("dialogLabel")}
        onClick={(event) => event.target === dialog.current && dialog.current?.close()}
        className="m-auto mt-[12vh] w-[min(36rem,calc(100vw-2rem))] rounded-xl border border-hairline bg-surface p-0 text-ink shadow-2xl backdrop:bg-black/40"
      >
        <div className="flex items-center gap-2 border-b border-hairline px-3">
          <svg aria-hidden="true" viewBox="0 0 16 16" className="size-4 text-ink-3">
            <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
            <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.5" />
          </svg>
          <input
            ref={input}
            role="combobox"
            aria-label={t("inputLabel")}
            aria-expanded={items.length > 0}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={current ? `${listId}-${current.id}` : undefined}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            placeholder={t("placeholder")}
            autoComplete="off"
            spellCheck={false}
            className="h-12 w-full bg-transparent text-[15px] text-ink outline-none placeholder:text-ink-3 focus-visible:outline-none"
          />
          <button
            type="button"
            onClick={() => dialog.current?.close()}
            aria-label={t("close")}
            className="rounded border border-hairline px-1.5 text-[11px] text-ink-3 hover:text-ink"
          >
            Esc
          </button>
        </div>
        <ul id={listId} role="listbox" aria-label={t("dialogLabel")} className="max-h-[50vh] overflow-y-auto pb-2">
          {renderGroup("companies", companies)}
          {renderGroup("pages", pages)}
        </ul>
        <p className="border-t border-hairline px-3 py-2 text-xs text-ink-3" aria-live="polite">
          {searching && state.status === "loading"
            ? t("loading")
            : searching && state.status === "error"
              ? t("error")
              : searching && state.status === "done" && companies.length === 0
                ? t("noResults", { query: query.trim() })
                : t("hint")}
        </p>
      </dialog>
    </>
  );
}
