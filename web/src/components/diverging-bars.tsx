"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState } from "react";

import { useFormat } from "@/lib/use-format";

export type FlowDatum = { period: string; buy: number; sell: number; buys: number; sells: number };

const MARGIN = { top: 12, right: 8, bottom: 30, left: 60 };

function niceStep(span: number, count: number) {
  const raw = span / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = [1, 2, 2.5, 5, 10].find((m) => m * power >= raw) ?? 10;
  return unit * power;
}

/** Bar path anchored on the baseline with 4px rounded corners at the data end only. */
function barPath(x: number, width: number, base: number, end: number) {
  const up = end < base;
  const height = Math.abs(base - end);
  const r = Math.min(4, height, width / 2);
  const x1 = x + width;
  return up
    ? `M${x},${base}V${end + r}A${r},${r} 0 0 1 ${x + r},${end}H${x1 - r}A${r},${r} 0 0 1 ${x1},${end + r}V${base}Z`
    : `M${x},${base}V${end - r}A${r},${r} 0 0 0 ${x + r},${end}H${x1 - r}A${r},${r} 0 0 0 ${x1},${end - r}V${base}Z`;
}

export function DivergingBars({ data, granularity, height = 260 }: {
  data: FlowDatum[];
  granularity: "week" | "month";
  height?: number;
}) {
  const t = useTranslations("flow");
  const tc = useTranslations("common");
  const format = useFormat();
  const wrapper = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [active, setActive] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const tableId = useId();

  // Tapping outside the chart closes a tooltip opened by touch.
  useEffect(() => {
    if (active === null) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setActive(null);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [active]);

  useEffect(() => {
    const element = wrapper.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const label = (iso: string) => (granularity === "week" ? format.dayMonth(iso) : format.month(iso));
  const longLabel = (iso: string) =>
    granularity === "week" ? t("weekOf", { date: format.dayMonth(iso) }) : format.month(iso);

  const maxBuy = Math.max(0, ...data.map((d) => d.buy));
  const maxSell = Math.max(0, ...data.map((d) => d.sell));
  const step = niceStep(maxBuy + maxSell || 1, 4);
  const top = Math.max(step, Math.ceil(maxBuy / step) * step);
  const bottom = -Math.max(step, Math.ceil(maxSell / step) * step);
  const ticks: number[] = [];
  for (let v = bottom; v <= top + step / 2; v += step) ticks.push(v);

  const plotWidth = width - MARGIN.left - MARGIN.right;
  const plotHeight = height - MARGIN.top - MARGIN.bottom;
  const y = (v: number) => MARGIN.top + ((top - v) / (top - bottom)) * plotHeight;
  const band = data.length ? plotWidth / data.length : plotWidth;
  const barWidth = Math.max(2, Math.min(24, band * 0.62));
  const every = Math.max(1, Math.ceil(68 / band));
  const zero = y(0);
  const point = active === null ? null : data[active];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-sm text-ink-2">
        <ul className="flex items-center gap-4" aria-label={tc("legend")}>
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="h-2.5 w-3 rounded-[2px] bg-buy" /> {t("buys")}
          </li>
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="h-2.5 w-3 rounded-[2px] bg-sell" /> {t("sells")}
          </li>
        </ul>
        <button
          type="button"
          onClick={() => setShowTable((v) => !v)}
          aria-expanded={showTable}
          aria-controls={tableId}
          className="rounded-md border border-hairline px-2.5 py-1 text-xs text-ink-2 hover:bg-surface-2 hover:text-ink"
        >
          {showTable ? tc("hideTable") : tc("showTable")}
        </button>
      </div>

      <div
        ref={wrapper}
        className="relative"
        // A finger "leaves" as soon as it lifts: keep the tapped period's tooltip until the next tap.
        onPointerLeave={(event) => event.pointerType === "mouse" && setActive(null)}
      >
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="block h-auto w-full"
          role="group"
          aria-label={t("ariaLabel")}
        >
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                x1={MARGIN.left}
                x2={width - MARGIN.right}
                y1={y(tick)}
                y2={y(tick)}
                stroke={tick === 0 ? "var(--baseline)" : "var(--hairline)"}
                strokeWidth={1}
              />
              <text x={MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end" className="tabular fill-ink-3 text-[11px]">
                {format.eurAxis(tick)}
              </text>
            </g>
          ))}

          {data.map((d, i) => {
            const x = MARGIN.left + band * i;
            const barX = x + (band - barWidth) / 2;
            return (
              <g key={d.period}>
                {active === i && (
                  <rect x={x} y={MARGIN.top} width={band} height={plotHeight} fill="var(--surface-2)" />
                )}
                {d.buy > 0 && zero - 1 - y(d.buy) >= 1 && (
                  <path d={barPath(barX, barWidth, zero - 1, y(d.buy))} fill="var(--buy)" />
                )}
                {d.sell > 0 && y(-d.sell) - (zero + 1) >= 1 && (
                  <path d={barPath(barX, barWidth, zero + 1, y(-d.sell))} fill="var(--sell)" />
                )}
                {i % every === 0 && (
                  <text x={x + band / 2} y={height - 10} textAnchor="middle" className="fill-ink-3 text-[11px]">
                    {label(d.period)}
                  </text>
                )}
                <rect
                  x={x}
                  y={MARGIN.top}
                  width={band}
                  height={plotHeight}
                  fill="transparent"
                  tabIndex={0}
                  role="img"
                  aria-label={t("point", {
                    period: longLabel(d.period),
                    buy: format.eur(d.buy),
                    buys: d.buys,
                    sell: format.eur(d.sell),
                    sells: d.sells,
                  })}
                  onPointerEnter={() => setActive(i)}
                  onPointerDown={() => setActive(i)}
                  onFocus={() => setActive(i)}
                  onBlur={() => setActive(null)}
                  className="cursor-default outline-none focus-visible:stroke-[var(--accent)] focus-visible:stroke-2"
                />
              </g>
            );
          })}
        </svg>

        {point && active !== null && (
          <div
            role="status"
            className="pointer-events-none absolute top-2 z-10 w-52 rounded-lg border border-hairline bg-surface p-3 text-xs shadow-lg"
            style={{
              left: Math.min(
                Math.max(0, MARGIN.left + band * (active + 0.5) - 104),
                width - 208,
              ),
            }}
          >
            <p className="text-ink-3">{longLabel(point.period)}</p>
            <p className="mt-2 flex items-center gap-2">
              <span aria-hidden="true" className="h-0.5 w-3 rounded bg-buy" />
              <strong className="text-sm font-semibold text-ink">{format.eur(point.buy)}</strong>
              <span className="text-ink-3">{t("buysCount", { count: format.int(point.buys) })}</span>
            </p>
            <p className="mt-1 flex items-center gap-2">
              <span aria-hidden="true" className="h-0.5 w-3 rounded bg-sell" />
              <strong className="text-sm font-semibold text-ink">{format.eur(point.sell)}</strong>
              <span className="text-ink-3">{t("sellsCount", { count: format.int(point.sells) })}</span>
            </p>
            <p className="mt-2 border-t border-hairline pt-2 text-ink-2">
              {t("net")} <strong className="font-semibold text-ink">{format.signedEur(point.buy - point.sell)}</strong>
            </p>
          </div>
        )}
      </div>

      <div id={tableId} hidden={!showTable} role="region" aria-label={t("ariaLabel")} tabIndex={0} className="mt-4 overflow-x-auto">
        <table className="tabular w-full min-w-[34rem] text-sm">
          <thead className="text-left text-ink-3">
            <tr className="border-b border-hairline">
              <th scope="col" className="py-2 pr-4 font-medium">{t("period")}</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">{t("buys")}</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">{t("buyTrades")}</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">{t("sells")}</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">{t("sellTrades")}</th>
              <th scope="col" className="py-2 text-right font-medium">{t("net")}</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.period} className="border-b border-hairline/60">
                <td className="py-1.5 pr-4 text-ink-2">{longLabel(d.period)}</td>
                <td className="py-1.5 pr-4 text-right">{format.eur(d.buy)}</td>
                <td className="py-1.5 pr-4 text-right">{format.int(d.buys)}</td>
                <td className="py-1.5 pr-4 text-right">{format.eur(d.sell)}</td>
                <td className="py-1.5 pr-4 text-right">{format.int(d.sells)}</td>
                <td className="py-1.5 text-right">{format.signedEur(d.buy - d.sell)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
