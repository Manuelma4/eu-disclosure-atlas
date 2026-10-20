"use client";

import { useEffect, useId, useRef, useState } from "react";

import { formatEur, formatInt, formatSignedEur } from "@/lib/format";

export type FlowDatum = { period: string; buy: number; sell: number; buys: number; sells: number };

const MARGIN = { top: 12, right: 8, bottom: 30, left: 60 };
const dayMonth = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const monthYear = new Intl.DateTimeFormat("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" });
const eurAxis = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "EUR",
  notation: "compact",
  maximumFractionDigits: 1,
});

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
  const wrapper = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [active, setActive] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const tableId = useId();

  useEffect(() => {
    const element = wrapper.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const label = (iso: string) => (granularity === "week" ? dayMonth : monthYear).format(new Date(iso));
  const longLabel = (iso: string) =>
    granularity === "week" ? `Week of ${dayMonth.format(new Date(iso))}` : monthYear.format(new Date(iso));

  const maxBuy = Math.max(0, ...data.map((d) => d.buy));
  const maxSell = Math.max(0, ...data.map((d) => d.sell));
  const step = niceStep(maxBuy + maxSell || 1, 4);
  const top = Math.max(step, Math.ceil(maxBuy / step) * step);
  const bottom = -Math.max(step, Math.ceil(maxSell / step) * step);
  const ticks: number[] = [];
  for (let t = bottom; t <= top + step / 2; t += step) ticks.push(t);

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
        <ul className="flex items-center gap-4" aria-label="Legend">
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="h-2.5 w-3 rounded-[2px] bg-buy" /> Insider buys
          </li>
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="h-2.5 w-3 rounded-[2px] bg-sell" /> Insider sells
          </li>
        </ul>
        <button
          type="button"
          onClick={() => setShowTable((v) => !v)}
          aria-expanded={showTable}
          aria-controls={tableId}
          className="rounded-md border border-hairline px-2.5 py-1 text-xs text-ink-2 hover:bg-surface-2 hover:text-ink"
        >
          {showTable ? "Hide table" : "Show table"}
        </button>
      </div>

      <div ref={wrapper} className="relative" onPointerLeave={() => setActive(null)}>
        <svg width={width} height={height} role="img" aria-label="Insider buying and selling by period, in euros">
          {ticks.map((t) => (
            <g key={t}>
              <line
                x1={MARGIN.left}
                x2={width - MARGIN.right}
                y1={y(t)}
                y2={y(t)}
                stroke={t === 0 ? "var(--baseline)" : "var(--hairline)"}
                strokeWidth={1}
              />
              <text x={MARGIN.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="tabular fill-ink-3 text-[11px]">
                {t === 0 ? "€0" : `${t < 0 ? "−" : ""}${eurAxis.format(Math.abs(t))}`}
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
                  aria-label={`${longLabel(d.period)}: buys ${formatEur(d.buy)} in ${d.buys} trades, sells ${formatEur(d.sell)} in ${d.sells} trades`}
                  onPointerEnter={() => setActive(i)}
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
              <strong className="text-sm font-semibold text-ink">{formatEur(point.buy)}</strong>
              <span className="text-ink-3">buys · {formatInt(point.buys)}</span>
            </p>
            <p className="mt-1 flex items-center gap-2">
              <span aria-hidden="true" className="h-0.5 w-3 rounded bg-sell" />
              <strong className="text-sm font-semibold text-ink">{formatEur(point.sell)}</strong>
              <span className="text-ink-3">sells · {formatInt(point.sells)}</span>
            </p>
            <p className="mt-2 border-t border-hairline pt-2 text-ink-2">
              Net <strong className="font-semibold text-ink">{formatSignedEur(point.buy - point.sell)}</strong>
            </p>
          </div>
        )}
      </div>

      <div id={tableId} hidden={!showTable} className="mt-4 overflow-x-auto">
        <table className="tabular w-full text-sm">
          <thead className="text-left text-ink-3">
            <tr className="border-b border-hairline">
              <th className="py-2 pr-4 font-medium">Period</th>
              <th className="py-2 pr-4 text-right font-medium">Buys</th>
              <th className="py-2 pr-4 text-right font-medium">Buy trades</th>
              <th className="py-2 pr-4 text-right font-medium">Sells</th>
              <th className="py-2 pr-4 text-right font-medium">Sell trades</th>
              <th className="py-2 text-right font-medium">Net</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.period} className="border-b border-hairline/60">
                <td className="py-1.5 pr-4 text-ink-2">{longLabel(d.period)}</td>
                <td className="py-1.5 pr-4 text-right">{formatEur(d.buy)}</td>
                <td className="py-1.5 pr-4 text-right">{formatInt(d.buys)}</td>
                <td className="py-1.5 pr-4 text-right">{formatEur(d.sell)}</td>
                <td className="py-1.5 pr-4 text-right">{formatInt(d.sells)}</td>
                <td className="py-1.5 text-right">{formatSignedEur(d.buy - d.sell)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
