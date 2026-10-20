"""Do European insiders beat the market? Event study on the Swedish PDMR register (WS6).

One command, from the repository root:

    python research/insider_event_study/run.py --backfill

--backfill   first ingest the FI register since 3 July 2016 into the lake (resumable, ~2 hours the first time)
--lake PATH  lake root (default: ./lake)
--until DATE last publication date in the sample (default: everything in the lake); the committed results
             use --until 2026-10-20

Writes aggregated tables to results/ and figures to figures/ next to this file. Raw prices, ticker lookups
in progress and event-level data stay in cache/ (git-ignored). Not investment advice.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import eventstudy as es  # noqa: E402
import figures  # noqa: E402
from events import aggregate_events, load_trades  # noqa: E402
from prices import BENCHMARK, load_prices  # noqa: E402
from tickers import map_isins  # noqa: E402

RESULTS, FIGURES, CACHE = HERE / "results", HERE / "figures", HERE / "cache"
TICKER_MAP = HERE / "data" / "isin_tickers.csv"
PRICE_START = "2015-06-01"  # one estimation year before the register starts
PRICE_TOLERANCE = (2 / 3, 3 / 2)  # filing price vs market close on the trade date
SIZE_LABELS = ["Small", "Mid", "Large"]
TABLE_WINDOWS = ["[0,+5]", "[0,+20]", "[0,+60]"]


def log(message: str) -> None:
    print(message, flush=True)


def prepare(lake: Path, until: date | None = None) -> tuple[pd.DataFrame, es.Panel, dict]:
    from disclosure_atlas.lake import Lake

    if not Lake(lake).fx_path.exists():
        subprocess.run([sys.executable, "-m", "disclosure_atlas.cli", "--lake", str(lake), "reference", "fx"],
                       check=True)
    trades = load_trades(lake)
    if until is not None:
        trades = trades[trades["published_local"] < pd.Timestamp(until) + pd.Timedelta(days=1)]
    events = aggregate_events(trades)
    log(f"events: {len(trades)} open-market lines -> {len(events)} issuer-day events")
    coverage = {"open_market_lines": len(trades), "events": len(events),
                "first_publication": str(events["published_local"].min().date()),
                "last_publication": str(events["published_local"].max().date())}

    mapping = map_isins(events["isin"].unique(), TICKER_MAP, log=log)
    events = events.merge(mapping[["isin", "yahoo_ticker"]], on="isin", how="left")
    events["ticker"] = events["yahoo_ticker"].replace("", np.nan)
    coverage["isins"] = int(events["isin"].nunique())
    coverage["isins_mapped"] = int(events.loc[events["ticker"].notna(), "isin"].nunique())
    coverage["events_mapped"] = int(events["ticker"].notna().sum())

    prices = load_prices(sorted(events["ticker"].dropna().unique()), CACHE / "prices.parquet", PRICE_START, log=log)
    coverage["tickers_with_prices"] = int(prices.loc[prices["ticker"] != BENCHMARK, "ticker"].nunique())
    panel = es.build_panel(prices, BENCHMARK)

    # Mapping check, per ISIN: filing prices (SEK lines) should match the market close on the trade date, up to
    # a constant factor (Yahoo's Close is also adjusted for rights issues, which `raw_close` cannot undo).
    # An ISIN whose log price ratio is neither centred near 0 nor stable is treated as mis-mapped.
    raw = prices.set_index(["ticker", "date"])["raw_close"]
    keys = list(zip(events["ticker"], events["last_trade"], strict=True))
    market_price = pd.Series([raw.get(k, np.nan) for k in keys], index=events.index)
    log_ratio = np.log(events["vwap"] / market_price)
    sek = trades.groupby("isin")["currency"].agg(lambda s: (s == "SEK").all())
    checkable = events["isin"].map(sek).fillna(False).astype(bool) & log_ratio.notna()
    by_isin = log_ratio[checkable].groupby(events.loc[checkable, "isin"]).agg(["median", "std", "count"])
    centred = by_isin["median"].abs() <= np.log(PRICE_TOLERANCE[1])
    stable = (by_isin["count"] >= 3) & (by_isin["std"] <= 0.2)
    rejected = set(by_isin.index[~(centred | stable)])
    coverage["isins_price_checked"] = int(len(by_isin))
    coverage["isins_price_mismatch_dropped"] = len(rejected)
    coverage["events_price_mismatch_dropped"] = int(events["isin"].isin(rejected).sum())
    events.loc[events["isin"].isin(rejected), "ticker"] = np.nan
    coverage["events_untyped_instrument"] = int(events["inferred_share"].sum())
    coverage["events_untyped_instrument_kept"] = int((events["inferred_share"] & events["ticker"].notna()).sum())

    events["day0"] = es.day_zero(events["published_local"], panel.dates)
    events["cluster"] = es.flag_clusters(events)
    return events, panel, coverage


MODELS = {"market_model": "", "market_adjusted": "ma:"}  # model -> column prefix


def add_ars(events: pd.DataFrame, panel: es.Panel, coverage: dict) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    paths = {}
    for model, prefix in MODELS.items():
        cars, paths[model] = es.abnormal_returns(events, panel, model=model)
        if prefix:
            cars = cars[[*es.WINDOWS, "sigma", "alpha"]].add_prefix(prefix)
        events = pd.concat([events, cars], axis=1)
    usable = events["sigma"].notna()
    coverage["events_with_estimation_window"] = int(usable.sum())
    for name in TABLE_WINDOWS:
        coverage[f"events_usable_{name}"] = int(events[name].notna().sum())
    coverage["coverage_share_[0,+20]"] = round(events["[0,+20]"].notna().mean(), 4)
    # Firm-size proxy known at day 0: median daily traded value over the estimation window, terciles by year.
    year = events["published_local"].dt.year
    events["size_tercile"] = (events[usable].groupby(year[usable])["size_value"]
                              .transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=SIZE_LABELS)))
    events["period"] = pd.cut(events["published_local"], pd.to_datetime(["2016-01-01", "2020-01-01",
                              "2024-12-04", "2100-01-01"]), labels=["2016-2019", "2020-Nov 2024",
                              "Dec 2024-2026"], right=False)
    events["publication_time"] = publication_time(events["published_local"], panel.dates)
    return events, paths


def publication_time(published_local: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """Before the open (09:00), during the session, after the close (17:25), or on a non-trading day."""
    day = published_local.dt.normalize()
    clock = published_local - day
    label = np.where(clock < pd.Timedelta(hours=9), "Before the open",
                     np.where(clock < es.CLOSE_CUTOFF, "During the session", "After the close"))
    label = np.where(day.isin(dates), label, "Non-trading day")
    return pd.Series(label, index=published_local.index)


def subgroup_table(events: pd.DataFrame) -> pd.DataFrame:
    splits = {
        "All": None,
        "Role": "role_group",
        "Trade size": "size_bucket",
        "Cluster": "cluster",
        "Firm size": "size_tercile",
        "Period": "period",
        "Publication time": "publication_time",
    }
    rows = []
    for model, prefix in MODELS.items():
        for nature in ("buy", "sell"):
            side = events[events["nature"] == nature]
            for split, column in splits.items():
                groups = [("All", side)] if column is None else [
                    (str(k), g) for k, g in side.groupby(column, observed=True)]
                for label, group in groups:
                    if column == "cluster":
                        label = "Cluster (>= 2 insiders in 5 days)" if label == "True" else "Single insider"
                    for window, (w0, w1) in es.WINDOWS.items():
                        stats_ = es.summarize(group[prefix + window], group[prefix + "sigma"], w1 - w0 + 1)
                        rows.append({"model": model, "nature": nature, "split": split, "group": label,
                                     "window": window, **stats_})
    table = pd.DataFrame(rows)
    # Multiple testing: BH q-values over every subgroup test of the same model and window.
    table["q_bh"] = table.groupby(["model", "window"], group_keys=False)["p_bmp"].apply(es.benjamini_hochberg)
    return table


def calendar_table(events: pd.DataFrame, panel: es.Panel) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Calendar-time alphas against the market (one factor) and against the market plus an equal-weighted
    universe of all register stocks with prices (two factors: a control for the small-cap, equal-weight tilt)."""
    rows, series = [], {}
    usable = events[events["sigma"].notna()]
    universe = pd.Series(es.universe_return(panel), index=panel.dates, name="ew")
    for entry, horizon in [(0, 20), (0, 60), (1, 20), (1, 60)]:
        legs = {n: es.calendar_time(usable[usable["nature"] == n], panel, horizon, entry=entry)
                for n in ("buy", "sell")}
        both = legs["buy"].join(legs["sell"], lsuffix="_buy", rsuffix="_sell", how="inner")
        long_short = pd.DataFrame({"ret": both["ret_buy"] - both["ret_sell"],
                                   "mkt": both["mkt_buy"], "n": both["n_buy"] + both["n_sell"]})
        for name, frame in [("Buys", legs["buy"]), ("Sells", legs["sell"]), ("Buys minus sells", long_short)]:
            if len(frame) < 30:
                continue
            frame = frame.join(universe).dropna(subset=["ew"])
            one = es.alpha_newey_west(frame["ret"], frame["mkt"])
            two = es.alpha_newey_west(frame["ret"], frame[["mkt", "ew"]])
            rows.append({"portfolio": name, "entry_day": entry, "holding_days": horizon, "days": one["days"],
                         "avg_stocks": frame["n"].mean(), "alpha_daily": one["alpha"],
                         "alpha_annual": (1 + one["alpha"]) ** 252 - 1, "t_nw": one["t"], "beta": one["beta"],
                         "alpha2_daily": two["alpha"], "alpha2_annual": (1 + two["alpha"]) ** 252 - 1,
                         "t2_nw": two["t"]})
            series[f"{name} ({horizon}d from day {entry})"] = frame
    return pd.DataFrame(rows), series


def markdown_tables(table: pd.DataFrame, calendar: pd.DataFrame, coverage: dict) -> str:
    def cell(r: pd.Series) -> str:
        if pd.isna(r["mean"]):
            return "n/a"
        stars = "**" if r["q_bh"] < 0.05 else ""
        return f"{stars}{100 * r['mean']:+.2f}%{stars} ({r['t_bmp']:.1f})"

    windows = ["[0,0]", "[0,+5]", "[0,+20]", "[0,+60]", "[+1,+20]", "[-20,-1]"]
    lines = []
    for model in MODELS:
        for nature in ("buy", "sell"):
            title = f"{'Purchases' if nature == 'buy' else 'Sales'}, {model.replace('_', '-')} abnormal returns"
            lines += [f"#### {title}", "",
                      "| Split | Group | N [0,+20] | " + " | ".join(f"CAR {w}" for w in windows) + " |",
                      "|---|---|---:|" + "---:|" * len(windows)]
            side = table[(table["nature"] == nature) & (table["model"] == model)]
            for (split, group), g in side.groupby(["split", "group"], sort=False):
                by = g.set_index("window")
                cells = " | ".join(cell(by.loc[w]) for w in windows)
                lines.append(f"| {split} | {group} | {int(by.loc['[0,+20]', 'n'])} | {cells} |")
            lines.append("")
    lines += ["#### Calendar-time portfolios (equal-weighted, rebalanced daily)", "",
              "| Portfolio | Entry day | Holding (days) | Days | Avg. stocks | Alpha/day, market | Annualized | t (NW) "
              "| Alpha/day, market + EW universe | Annualized | t (NW) |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for _, r in calendar.iterrows():
        lines.append(f"| {r['portfolio']} | {r['entry_day']} | {r['holding_days']} | {r['days']} | "
                     f"{r['avg_stocks']:.1f} | "
                     f"{1e4 * r['alpha_daily']:+.1f} bp | {100 * r['alpha_annual']:+.1f}% | {r['t_nw']:.2f} | "
                     f"{1e4 * r['alpha2_daily']:+.1f} bp | {100 * r['alpha2_annual']:+.1f}% | {r['t2_nw']:.2f} |")
    lines += ["", "#### Coverage", "", "| Item | Value |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in coverage.items()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lake", type=Path, default=Path("lake"))
    parser.add_argument("--backfill", action="store_true", help="ingest the FI register first (resumable)")
    parser.add_argument("--until", type=date.fromisoformat, help="last publication date in the sample")
    args = parser.parse_args(argv)
    if args.backfill:
        subprocess.run(["bash", str(HERE / "backfill.sh")], check=True, env={**_env(), "LAKE": str(args.lake)})

    events, panel, coverage = prepare(args.lake, args.until)
    events, paths = add_ars(events, panel, coverage)
    table = subgroup_table(events)
    calendar, series = calendar_table(events, panel)
    coverage["sample_until"] = args.until.isoformat() if args.until else None

    RESULTS.mkdir(exist_ok=True)
    FIGURES.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    table.round(6).to_csv(RESULTS / "car_by_group.csv", index=False)
    calendar.round(6).to_csv(RESULTS / "calendar_time.csv", index=False)
    yearly = (events[events["[0,+20]"].notna()].groupby([events["published_local"].dt.year.rename("year"),
              "nature"])[["[0,+20]", "ma:[0,+20]"]].agg(["count", "mean", "median"]))
    yearly.columns = [f"{window}_{stat}" for window, stat in yearly.columns]
    yearly = yearly.reset_index()
    yearly.round(6).to_csv(RESULTS / "car20_by_year.csv", index=False)
    flagged = events.assign(year=events["published_local"].dt.year, mapped=events["ticker"].notna(),
                            usable=events["[0,+20]"].notna())
    for key in ("year", "market"):
        (flagged.groupby(key).agg(events=("isin", "size"), mapped=("mapped", "sum"), usable_car20=("usable", "sum"))
         .to_csv(RESULTS / f"coverage_by_{key}.csv"))
    (RESULTS / "coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
    (RESULTS / "tables.md").write_text(markdown_tables(table, calendar, coverage))
    # Event-level data stays local: it carries pseudonymous insider ids.
    events.drop(columns=["pdmr_ids"]).to_parquet(CACHE / "events.parquet", index=False)

    figures.car_paths(events, paths["market_model"], FIGURES / "car_buys_sells.png")
    figures.car_paths(events, paths["market_adjusted"], FIGURES / "car_buys_sells_market_adjusted.png",
                      subtitle="market-adjusted")
    figures.car_paths(events, paths["market_adjusted"], FIGURES / "car_cluster_buys.png", by="cluster",
                      subtitle="market-adjusted")
    figures.calendar_cumulative(series, FIGURES / "calendar_time.png")
    log(json.dumps(coverage, indent=2))
    log(f"results -> {RESULTS}, figures -> {FIGURES}")
    return 0


def _env() -> dict[str, str]:
    import os

    path = Path(sys.executable).parent
    return {**os.environ, "PATH": f"{path}{os.pathsep}{os.environ.get('PATH', '')}"}


if __name__ == "__main__":
    sys.exit(main())
