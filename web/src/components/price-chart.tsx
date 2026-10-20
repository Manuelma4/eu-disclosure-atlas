"use client";

import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { useTranslations } from "next-intl";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import type { PriceSeries } from "@/lib/api/data";
import { useFormat } from "@/lib/use-format";

type Day = {
  date: string;
  close: number;
  buys: number;
  sells: number;
  buyEur: number | null;
  sellEur: number | null;
};

const HEIGHT = 300;

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Beside the crosshair, on the side with more room, so the tooltip never hides the active day. */
const tooltipLeft = (x: number, width: number) => Math.max(0, x > width / 2 ? x - 208 - 16 : x + 16);

const toTime = (iso: string) => (Date.parse(`${iso}T00:00:00Z`) / 1000) as UTCTimestamp;

/**
 * Daily closing price (TradingView lightweight-charts) with insider buy and sell markers. Keyboard: arrows move
 * one trading day, Page Up/Down jump between insider trades, Home/End go to the ends. Every value is also in
 * the table view.
 */
export function PriceChart({ series, issuerName }: { series: PriceSeries; issuerName: string }) {
  const t = useTranslations("price");
  const tc = useTranslations("common");
  const format = useFormat();
  const container = useRef<HTMLDivElement>(null);
  const chartRef = useRef<{ chart: IChartApi; line: ISeriesApi<"Line"> } | null>(null);
  const [hover, setHover] = useState<{ index: number; left: number } | null>(null);
  const active = hover?.index ?? null;
  const [showTable, setShowTable] = useState(false);
  const tableId = useId();

  // Bars joined with the insider activity of the same day; insider days without a bar (holidays) are dropped
  // from the chart but kept in the table.
  const days: Day[] = useMemo(() => {
    const insider = new Map(series.insider.map((d) => [d.date, d]));
    return series.bars.map((b) => {
      const d = insider.get(b.date);
      return {
        date: b.date,
        close: b.close,
        buys: d?.buys ?? 0,
        sells: d?.sells ?? 0,
        buyEur: d?.buy_eur ?? null,
        sellEur: d?.sell_eur ?? null,
      };
    });
  }, [series]);
  const tradeIndexes = useMemo(() => days.flatMap((d, i) => (d.buys || d.sells ? [i] : [])), [days]);
  const closeByDate = useMemo(() => new Map(series.bars.map((b) => [b.date, b.close])), [series]);
  const price = useMemo(
    () =>
      new Intl.NumberFormat(format.locale, {
        style: series.currency ? "currency" : "decimal",
        currency: series.currency ?? undefined,
        maximumFractionDigits: 2,
      }),
    [format.locale, series.currency],
  );

  useEffect(() => {
    const element = container.current;
    if (!element || !days.length) return;
    let disposed = false;
    let cleanup = () => {};

    (async () => {
      const lw = await import("lightweight-charts");
      if (disposed) return;
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const chart = lw.createChart(element, {
        height: HEIGHT,
        autoSize: true,
        localization: { locale: format.locale, priceFormatter: (v: number) => price.format(v) },
        rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.12 } },
        timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true },
        handleScale: { mouseWheel: false },
        handleScroll: { mouseWheel: false, pressedMouseMove: true },
        kineticScroll: { mouse: !reduceMotion, touch: !reduceMotion },
        crosshair: { mode: lw.CrosshairMode.Magnet },
      });
      const line = chart.addSeries(lw.LineSeries, {
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerRadius: 3,
      });
      line.setData(days.map((d) => ({ time: toTime(d.date), value: d.close })));
      const markers = lw.createSeriesMarkers(line, []);

      const paint = () => {
        const ink3 = cssVar("--ink-3");
        const hairline = cssVar("--hairline");
        const buy = cssVar("--buy");
        const sell = cssVar("--sell");
        chart.applyOptions({
          layout: {
            background: { type: lw.ColorType.Solid, color: "transparent" },
            textColor: ink3,
            fontFamily: getComputedStyle(document.body).fontFamily,
            fontSize: 11,
          },
          grid: { vertLines: { visible: false }, horzLines: { color: hairline } },
          crosshair: {
            vertLine: { color: cssVar("--baseline"), labelBackgroundColor: cssVar("--ink-2") },
            horzLine: { color: cssVar("--baseline"), labelBackgroundColor: cssVar("--ink-2") },
          },
        });
        line.applyOptions({ color: cssVar("--ink-2") });
        markers.setMarkers(
          days.flatMap((d) => [
            ...(d.buys ? [{ time: toTime(d.date), position: "belowBar" as const, shape: "arrowUp" as const, color: buy, size: 1 }] : []),
            ...(d.sells ? [{ time: toTime(d.date), position: "aboveBar" as const, shape: "arrowDown" as const, color: sell, size: 1 }] : []),
          ]),
        );
      };
      paint();
      chart.timeScale().fitContent();

      const scheme = window.matchMedia("(prefers-color-scheme: dark)");
      scheme.addEventListener("change", paint);
      const indexByTime = new Map(days.map((d, i) => [toTime(d.date), i]));
      chart.subscribeCrosshairMove((param) => {
        const index = param.time === undefined ? undefined : indexByTime.get(param.time as UTCTimestamp);
        setHover(
          index === undefined || !param.point ? null : { index, left: tooltipLeft(param.point.x, element.clientWidth) },
        );
      });
      chartRef.current = { chart, line };
      cleanup = () => {
        scheme.removeEventListener("change", paint);
        chartRef.current = null;
        chart.remove();
      };
    })();

    return () => {
      disposed = true;
      cleanup();
    };
  }, [days, format.locale, price]);

  const moveTo = (index: number) => {
    const target = Math.max(0, Math.min(days.length - 1, index));
    const ref = chartRef.current;
    if (!ref) return;
    const scale = ref.chart.timeScale();
    const range = scale.getVisibleLogicalRange();
    if (range && (target < range.from || target > range.to)) {
      const span = range.to - range.from;
      scale.setVisibleLogicalRange({ from: target - span / 2, to: target + span / 2 });
    }
    // Setting the crosshair fires subscribeCrosshairMove, which updates the tooltip position.
    ref.chart.setCrosshairPosition(days[target].close, toTime(days[target].date), ref.line);
    setHover({
      index: target,
      left: tooltipLeft(scale.logicalToCoordinate(target as never) ?? 0, container.current?.clientWidth ?? 0),
    });
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    const current = active ?? days.length - 1;
    const keys: Record<string, () => number | undefined> = {
      ArrowLeft: () => current - 1,
      ArrowRight: () => current + 1,
      Home: () => 0,
      End: () => days.length - 1,
      PageUp: () => [...tradeIndexes].reverse().find((i) => i < current),
      PageDown: () => tradeIndexes.find((i) => i > current),
    };
    const next = keys[event.key]?.();
    if (event.key in keys) event.preventDefault();
    if (next !== undefined) moveTo(next);
  };

  if (!series.bars.length) {
    return <p className="text-sm text-ink-3">{t("noPrices")}</p>;
  }

  const point = active === null ? null : days[active];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-sm text-ink-2">
        <ul className="flex flex-wrap items-center gap-4" aria-label={tc("legend")}>
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="h-0.5 w-3 rounded bg-ink-2" /> {t("close")}
          </li>
          <li className="flex items-center gap-1.5">
            <svg aria-hidden="true" viewBox="0 0 10 10" className="size-2.5 fill-buy">
              <path d="M5 0l5 6H6.5v4h-3V6H0z" />
            </svg>
            {t("buyDay")}
          </li>
          <li className="flex items-center gap-1.5">
            <svg aria-hidden="true" viewBox="0 0 10 10" className="size-2.5 fill-sell">
              <path d="M5 10L0 4h3.5V0h3v4H10z" />
            </svg>
            {t("sellDay")}
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
        className="relative rounded-md focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
        tabIndex={0}
        role="group"
        aria-roledescription="chart"
        aria-label={t("ariaLabel", { name: issuerName })}
        onKeyDown={onKeyDown}
        onBlur={() => {
          chartRef.current?.chart.clearCrosshairPosition();
          setHover(null);
        }}
        data-testid="price-chart"
      >
        <div ref={container} style={{ height: HEIGHT }} />
        <div
          role="status"
          aria-live="polite"
          className={`pointer-events-none absolute top-1 z-10 w-52 rounded-lg border border-hairline bg-surface p-3 text-xs shadow-lg ${point ? "" : "sr-only"}`}
          style={hover ? { left: hover.left } : undefined}
        >
          {point && (
            <>
              <p className="text-ink-3">{format.date(point.date)}</p>
              <p className="mt-1.5 flex items-baseline justify-between gap-2">
                <span className="text-ink-3">{t("close")}</span>
                <strong className="text-sm font-semibold text-ink">{price.format(point.close)}</strong>
              </p>
              {point.buys > 0 && (
                <p className="mt-1 flex items-center gap-2">
                  <span aria-hidden="true" className="h-0.5 w-3 rounded bg-buy" />
                  <span className="text-ink-2">
                    {t("boughtEur")} <strong className="font-semibold text-ink">{format.eur(point.buyEur)}</strong> ·{" "}
                    {t("trades", { count: point.buys })}
                  </span>
                </p>
              )}
              {point.sells > 0 && (
                <p className="mt-1 flex items-center gap-2">
                  <span aria-hidden="true" className="h-0.5 w-3 rounded bg-sell" />
                  <span className="text-ink-2">
                    {t("soldEur")} <strong className="font-semibold text-ink">{format.eur(point.sellEur)}</strong> ·{" "}
                    {t("trades", { count: point.sells })}
                  </span>
                </p>
              )}
              {!point.buys && !point.sells && <p className="mt-1 text-ink-3">{t("noTrade")}</p>}
            </>
          )}
        </div>
      </div>

      <p className="mt-2 text-xs text-ink-3">
        {t("source", {
          ticker: series.ticker ?? "–",
          currency: series.currency ?? "–",
          provider: series.provider,
          date: format.date(series.fetched_at),
        })}
      </p>

      <div id={tableId} hidden={!showTable} className="mt-4 max-h-80 overflow-auto">
        <table className="tabular w-full text-sm">
          <caption className="sr-only">{t("tableCaption")}</caption>
          <thead className="sticky top-0 bg-surface text-left text-ink-3">
            <tr className="border-b border-hairline">
              <th scope="col" className="py-2 pr-4 font-medium">{t("date")}</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">{t("close")}</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">{t("buys")}</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">{t("boughtEur")}</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">{t("sells")}</th>
              <th scope="col" className="py-2 text-right font-medium">{t("soldEur")}</th>
            </tr>
          </thead>
          <tbody>
            {[...series.insider].reverse().map((d) => (
              <tr key={d.date} className="border-b border-hairline/60">
                <td className="py-1.5 pr-4 text-ink-2">{format.date(d.date)}</td>
                <td className="py-1.5 pr-4 text-right">
                  {closeByDate.has(d.date) ? price.format(closeByDate.get(d.date)!) : "–"}
                </td>
                <td className="py-1.5 pr-4 text-right">{format.int(d.buys)}</td>
                <td className="py-1.5 pr-4 text-right">{format.eur(d.buy_eur)}</td>
                <td className="py-1.5 pr-4 text-right">{format.int(d.sells)}</td>
                <td className="py-1.5 text-right">{format.eur(d.sell_eur)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
