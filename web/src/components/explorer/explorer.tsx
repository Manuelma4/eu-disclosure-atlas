"use client";

import type { Selection } from "@uwdata/mosaic-core";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";

import { useFormat, useLabels } from "@/lib/use-format";

import type { Row } from "./clients";
import { loadFilings, type Labels, type Loaded } from "./engine";

type Stage = "engine" | "download" | "ready" | "error";
type TableKey = "week" | "role" | "amount";
const ROLE_CODES = ["ceo", "cfo", "chair", "executive", "board_member", "other"];

/**
 * Linked, cross-filtering views over every filing, computed in the browser by DuckDB-WASM through Mosaic:
 * filings by week (brush), by role (click, or the keyboard chips) and the amount distribution (brush).
 */
export function Explorer() {
  const t = useTranslations("explore");
  const format = useFormat();
  const roleLabels = useLabels();
  const labels: Labels = useMemo(
    () => ({ buy: t("buy"), sell: t("sell"), other: t("other"), role: roleLabels.role }),
    [t, roleLabels],
  );

  const [stage, setStage] = useState<Stage>("engine");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<Loaded | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [summary, setSummary] = useState<{ n: number; eur: number | null } | null>(null);
  const [weeks, setWeeks] = useState<Row[]>([]);
  const [roles, setRoles] = useState<Row[]>([]);
  const [amounts, setAmounts] = useState<Row[]>([]);
  const [pressed, setPressed] = useState<string[]>([]);
  const selectionRef = useRef<Selection | null>(null);
  // Table views query only while they are open, so brushing a million rows stays fast.
  const tableClients = useRef<Partial<Record<TableKey, { enabled: boolean }>>>({});
  const openTables = useRef(new Set<string>());
  const roleSource = useRef<{ reset: () => void }>({ reset: () => setPressed([]) });
  const weekPlot = useRef<HTMLDivElement>(null);
  const rolePlot = useRef<HTMLDivElement>(null);
  const amountPlot = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    let teardown = () => {};
    (async () => {
      try {
        const loadedInfo = await loadFilings(labels, (s) => !cancelled && setStage(s));
        const vg = await import("@uwdata/vgplot");
        const { RowsClient } = await import("./clients");
        const { getEngine } = await import("./engine");
        if (cancelled) return;
        const { db, connection } = await getEngine();
        const coordinator = vg.coordinator();
        coordinator.databaseConnector(new vg.DuckDBWASMConnector({ duckdb: db, connection }));

        const selection = vg.Selection.crossfilter();
        selectionRef.current = selection;
        const domain = [labels.buy, labels.sell, labels.other];
        const range = ["var(--buy)", "var(--sell)", "var(--other)"];
        const common = (el: HTMLElement | null, height: number) => [
          vg.width(Math.max(320, el?.clientWidth ?? 640)),
          vg.height(height),
          vg.colorDomain(domain),
          vg.colorRange(range),
          vg.style({ fontFamily: "inherit", fontSize: "11px", overflow: "visible" }),
        ];
        const count = vg.count();

        const week = vg.plot(
          vg.rectY(vg.from("filings", { filterBy: selection }), {
            x: vg.bin("published", { interval: "week" }),
            y: count,
            fill: "side",
            order: domain,
            inset: 0.5,
            tip: true,
          }),
          vg.intervalX({ as: selection, brush: { fill: "var(--ink-3)", fillOpacity: 0.12, stroke: "var(--ink-2)" } }),
          vg.xLabel(null),
          vg.yLabel(t("count")),
          vg.yTickFormat("~s"),
          vg.xTickFormat((d: Date) => format.month(d.toISOString())),
          vg.marginLeft(44),
          vg.yGrid(true),
          ...common(weekPlot.current, 220),
        );
        const role = vg.plot(
          vg.barX(vg.from("filings", { filterBy: selection }), {
            x: count,
            y: "role",
            fill: "side",
            order: domain,
            sort: { y: "-x" },
            tip: true,
          }),
          vg.toggleY({ as: selection }),
          vg.xLabel(t("count")),
          vg.yLabel(null),
          vg.xTickFormat("~s"),
          vg.marginLeft(104),
          vg.xGrid(true),
          ...common(rolePlot.current, 240),
        );
        const amount = vg.plot(
          vg.rectY(vg.from("priced", { filterBy: selection }), {
            x: vg.bin("amount", { steps: 36 }),
            y: count,
            fill: "side",
            order: domain,
            inset: 0.5,
            tip: true,
          }),
          vg.intervalX({ as: selection, brush: { fill: "var(--ink-3)", fillOpacity: 0.12, stroke: "var(--ink-2)" } }),
          vg.xScale("log"),
          vg.xLabel(t("amountRange")),
          vg.yLabel(t("count")),
          vg.yTickFormat("~s"),
          vg.xTickFormat((v: number) => format.eur(v)),
          vg.marginLeft(44),
          vg.yGrid(true),
          ...common(amountPlot.current, 240),
        );
        for (const [ref, el, label] of [
          [weekPlot, week, t("weekTitle")],
          [rolePlot, role, t("roleTitle")],
          [amountPlot, amount, t("amountTitle")],
        ] as const) {
          el.setAttribute("aria-hidden", "true"); // the table views carry the same data for assistive tech
          el.dataset.chart = label;
          ref.current?.replaceChildren(el);
        }

        const summaryClient = new RowsClient(
            { from: "filings", select: { n: "count(*)", eur: `sum(amount) FILTER (WHERE side <> '${labels.other.replaceAll("'", "''")}')` } },
            selection,
            (rows) => setSummary({ n: Number(rows[0]?.n ?? 0), eur: rows[0]?.eur == null ? null : Number(rows[0].eur) }),
        );
        const tables = {
          week: new RowsClient(
            {
              from: "filings",
              select: {
                week: "CAST(date_trunc('week', published) AS DATE)",
                ...sideCounts(labels),
              },
              groupby: ["week"],
              orderby: ["week DESC"],
            },
            selection,
            setWeeks,
          ),
          role: new RowsClient(
            { from: "filings", select: { role: "role", ...sideCounts(labels) }, groupby: ["role"], orderby: ["n DESC"] },
            selection,
            setRoles,
          ),
          amount: new RowsClient(
            {
              from: "priced",
              select: { decade: "CAST(floor(log10(amount)) AS INTEGER)", ...sideCounts(labels) },
              groupby: ["decade"],
              orderby: ["decade"],
            },
            selection,
            setAmounts,
          ),
        };
        for (const [key, client] of Object.entries(tables)) {
          client.enabled = openTables.current.has(key);
          tableClients.current[key as keyof typeof tables] = client;
        }
        [summaryClient, ...Object.values(tables)].forEach((c) => coordinator.connect(c));
        setInfo(loadedInfo);
        setStage("ready");
        teardown = () => {
          coordinator.clear({ clients: true });
          selectionRef.current = null;
        };
      } catch (cause) {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : String(cause));
        setStage("error");
      }
    })();
    return () => {
      cancelled = true;
      teardown();
    };
  }, [labels, t, format, attempt]);

  const toggleRole = useCallback(
    (label: string) => {
      const selection = selectionRef.current;
      if (!selection) return;
      const next = pressed.includes(label) ? pressed.filter((r) => r !== label) : [...pressed, label];
      setPressed(next);
      import("@uwdata/mosaic-core").then(({ clausePoints }) => {
        selection.update(
          clausePoints(["role"], next.length ? next.map((r) => [r]) : null, { source: roleSource.current }),
        );
      });
    },
    [pressed],
  );

  const clear = () => selectionRef.current?.reset();
  const onTable = useCallback((key: TableKey, open: boolean) => {
    if (open) openTables.current.add(key);
    else openTables.current.delete(key);
    const client = tableClients.current[key];
    if (client) client.enabled = open;
  }, []);

  if (stage === "error") {
    return (
      <div role="alert" className="rounded-xl border border-hairline bg-surface p-6 text-sm">
        <p className="text-ink">{t("failed", { message: error ?? "" })}</p>
        <button
          type="button"
          onClick={() => {
            setError(null);
            setStage("engine");
            setAttempt((a) => a + 1);
          }}
          className="mt-4 rounded-md bg-ink px-3 py-1.5 font-medium text-page hover:opacity-90"
        >
          {t("retry")}
        </button>
      </div>
    );
  }

  const ready = stage === "ready" && info;
  const sideHeads = [labels.buy, labels.sell, labels.other];

  return (
    <div className="grid gap-6" aria-busy={!ready}>
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-hairline bg-surface px-5 py-3 text-sm">
        <p aria-live="polite" className="text-ink-2" data-testid="explore-status">
          {ready
            ? t("stage_ready", { rows: format.int(info.rows), seconds: format.decimal(info.seconds) })
            : t(stage === "download" ? "stage_download" : "stage_engine")}
        </p>
        {ready && summary && (
          <p className="text-ink" aria-live="polite" data-testid="explore-selected">
            <strong className="font-semibold">
              {t("selected", { count: format.int(summary.n), total: format.int(info.rows) })}
            </strong>
            <span className="text-ink-3"> · {t("selectedEur", { amount: format.eur(summary.eur) })}</span>
          </p>
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={clear}
            disabled={!ready}
            className="rounded-md border border-hairline px-2.5 py-1 text-xs text-ink-2 hover:bg-surface-2 hover:text-ink disabled:opacity-50"
          >
            {t("reset")}
          </button>
          {ready && (
            <a
              href={info.url}
              download
              className="rounded-md border border-hairline px-2.5 py-1 text-xs text-ink-2 hover:bg-surface-2 hover:text-ink"
            >
              {t("download")}
            </a>
          )}
        </div>
      </div>

      <View
        title={t("weekTitle")}
        subtitle={t("weekSubtitle")}
        legend={sideHeads}
        plot={weekPlot}
        tableKey="week"
        onTable={onTable}
        loading={!ready}
        height={220}
        table={
          <SideTable
            caption={t("weekTitle")}
            head={t("week")}
            rows={weeks}
            cell={(r) => format.date(String(r.week instanceof Date ? r.week.toISOString() : r.week))}
            sides={sideHeads}
          />
        }
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <View
          title={t("roleTitle")}
          subtitle={t("roleSubtitle")}
          legend={sideHeads}
          plot={rolePlot}
          tableKey="role"
        onTable={onTable}
          loading={!ready}
          height={240}
          controls={
            <div role="group" aria-label={t("roleTitle")} className="mb-3 flex flex-wrap gap-1.5">
              {ROLE_CODES.map((code) => {
                const label = roleLabels.role(code);
                const on = pressed.includes(label);
                return (
                  <button
                    key={code}
                    type="button"
                    aria-pressed={on}
                    disabled={!ready}
                    onClick={() => toggleRole(label)}
                    className="rounded-full border border-hairline px-2.5 py-0.5 text-xs text-ink-2 hover:text-ink aria-pressed:border-ink aria-pressed:bg-ink aria-pressed:text-page disabled:opacity-50"
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          }
          table={<SideTable caption={t("roleTitle")} head={t("roleTitle")} rows={roles} cell={(r) => String(r.role)} sides={sideHeads} />}
        />
        <View
          title={t("amountTitle")}
          subtitle={t("amountSubtitle")}
          legend={sideHeads}
          plot={amountPlot}
          tableKey="amount"
        onTable={onTable}
          loading={!ready}
          height={240}
          table={
            <SideTable
              caption={t("amountTitle")}
              head={t("amountRange")}
              rows={amounts}
              cell={(r) => `${format.eur(10 ** Number(r.decade))} – ${format.eur(10 ** (Number(r.decade) + 1))}`}
              sides={sideHeads}
            />
          }
        />
      </div>

      <p className="text-xs text-ink-3">
        {t("keyboard")}{" "}
        {ready &&
          t("source", { path: info.file.path, snapshot: info.file.snapshot, size: format.megabytes(info.file.bytes) })}
      </p>
    </div>
  );
}

function sideCounts(labels: Labels): Record<string, string> {
  const q = (v: string) => `'${v.replaceAll("'", "''")}'`;
  return {
    buys: `count(*) FILTER (WHERE side = ${q(labels.buy)})`,
    sells: `count(*) FILTER (WHERE side = ${q(labels.sell)})`,
    others: `count(*) FILTER (WHERE side = ${q(labels.other)})`,
    n: "count(*)",
  };
}

function View({
  title,
  subtitle,
  legend,
  plot,
  table,
  controls,
  tableKey,
  onTable,
  loading,
  height,
}: {
  title: string;
  subtitle: string;
  legend: string[];
  plot: React.RefObject<HTMLDivElement | null>;
  table: ReactNode;
  controls?: ReactNode;
  tableKey: TableKey;
  onTable: (key: TableKey, open: boolean) => void;
  loading: boolean;
  height: number;
}) {
  const tc = useTranslations("common");
  const [showTable, setShowTable] = useState(false);
  const tableId = useId();
  const swatch = ["bg-buy", "bg-sell", "bg-other"];
  return (
    <section className="rounded-xl border border-hairline bg-surface" aria-label={title}>
      <header className="flex items-start justify-between gap-4 px-5 pt-4">
        <div>
          <h2 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h2>
          <p className="mt-0.5 text-sm text-ink-3">{subtitle}</p>
        </div>
        <button
          type="button"
          onClick={() => {
            onTable(tableKey, !showTable);
            setShowTable(!showTable);
          }}
          aria-expanded={showTable}
          aria-controls={tableId}
          className="shrink-0 rounded-md border border-hairline px-2.5 py-1 text-xs text-ink-2 hover:bg-surface-2 hover:text-ink"
        >
          {showTable ? tc("hideTable") : tc("showTable")}
        </button>
      </header>
      <div className="px-5 pb-5 pt-3">
        <ul className="mb-3 flex flex-wrap items-center gap-4 text-sm text-ink-2" aria-label={tc("legend")}>
          {legend.map((label, i) => (
            <li key={label} className="flex items-center gap-1.5">
              <span aria-hidden="true" className={`h-2.5 w-3 rounded-[2px] ${swatch[i]}`} /> {label}
            </li>
          ))}
        </ul>
        {controls}
        <div className="relative" style={{ minHeight: height }}>
          <div ref={plot} className="explorer-plot text-ink-3" />
          {loading && <div aria-hidden="true" className="absolute inset-0 animate-pulse rounded-lg bg-surface-2" />}
        </div>
        <div id={tableId} hidden={!showTable} className="mt-4 max-h-80 overflow-auto">
          {table}
        </div>
      </div>
    </section>
  );
}

function SideTable({
  caption,
  head,
  rows,
  cell,
  sides,
}: {
  caption: string;
  head: string;
  rows: Row[];
  cell: (row: Row) => string;
  sides: string[];
}) {
  const t = useTranslations("explore");
  const format = useFormat();
  return (
    <table className="tabular w-full text-sm">
      <caption className="sr-only">{caption}</caption>
      <thead className="sticky top-0 bg-surface text-left text-ink-3">
        <tr className="border-b border-hairline">
          <th scope="col" className="py-2 pr-4 font-medium">{head}</th>
          {sides.map((s) => (
            <th key={s} scope="col" className="py-2 pr-4 text-right font-medium">
              {s}
            </th>
          ))}
          <th scope="col" className="py-2 text-right font-medium">{t("count")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-hairline/60">
            <td className="py-1.5 pr-4 text-ink-2">{cell(r)}</td>
            <td className="py-1.5 pr-4 text-right">{format.int(Number(r.buys))}</td>
            <td className="py-1.5 pr-4 text-right">{format.int(Number(r.sells))}</td>
            <td className="py-1.5 pr-4 text-right">{format.int(Number(r.others))}</td>
            <td className="py-1.5 text-right">{format.int(Number(r.n))}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
