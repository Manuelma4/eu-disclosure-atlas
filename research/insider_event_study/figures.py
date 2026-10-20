"""Static figures for the write-up (matplotlib, PNG)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from eventstudy import PATH

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _axes(title: str, ylabel: str):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.axhline(0, color=MUTED, linewidth=0.8)
    ax.set_title(title, loc="left", color=INK, fontsize=12)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=1))
    return fig, ax


def _mean_path(paths: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    complete = np.isfinite(paths).mean(axis=1) >= 0.8
    cum = np.nancumsum(paths[complete], axis=1)
    n = len(cum)
    return cum.mean(axis=0), 1.96 * cum.std(axis=0, ddof=1) / np.sqrt(max(n, 1)), n


def car_paths(events: pd.DataFrame, paths: np.ndarray, out: Path, by: str = "nature",
              subtitle: str = "market model") -> None:
    days = np.arange(PATH[0], PATH[1] + 1)
    if by == "nature":
        groups = [("Purchases", events["nature"] == "buy", BLUE), ("Sales", events["nature"] == "sell", ORANGE)]
        title = "Mean cumulative abnormal return after publication"
    else:
        buys = events["nature"] == "buy"
        groups = [("Cluster purchases", buys & events["cluster"], AQUA),
                  ("Single-insider purchases", buys & ~events["cluster"], BLUE)]
        title = "Cluster versus single-insider purchases"
    fig, ax = _axes(f"{title} ({subtitle})", "CAR from day -20")
    for label, mask, color in groups:
        mean, ci, n = _mean_path(paths[mask.to_numpy()])
        if n < 2:
            continue
        ax.fill_between(days, mean - ci, mean + ci, color=color, alpha=0.15, linewidth=0)
        ax.plot(days, mean, color=color, linewidth=2)
        ax.annotate(f"{label} (N={n:,})", (days[-1], mean[-1]), xytext=(4, 0), textcoords="offset points",
                    color=INK, fontsize=9, va="center")
    ax.axvline(0, color=MUTED, linewidth=0.8, linestyle="--")
    ax.set_xlabel("Trading days relative to publication (day 0); shaded: 95% confidence band",
                  color=MUTED, fontsize=9)
    ax.set_xlim(days[0], days[-1] + 32)
    ax.set_xticks(range(-20, 61, 10))
    fig.tight_layout()
    fig.savefig(out)


def calendar_cumulative(series: dict[str, pd.DataFrame], out: Path) -> None:
    fig, ax = _axes("Calendar-time portfolios: cumulative return in excess of the market",
                    "Cumulative (portfolio - OMX Stockholm All-Share)")
    colors = {"Buys (20d from day 1)": BLUE, "Sells (20d from day 1)": ORANGE}
    for name, color in colors.items():
        if name not in series:
            continue
        frame = series[name]
        cum = (frame["ret"] - frame["mkt"]).cumsum()
        ax.plot(cum.index, cum.to_numpy(), color=color, linewidth=2)
        label = {"Buys": "Recent purchases", "Sells": "Recent sales"}[name.split()[0]]
        ax.annotate(label, (cum.index[-1], cum.iloc[-1]), xytext=(4, 0), textcoords="offset points",
                    color=INK, fontsize=9, va="center")
    ax.set_xlabel("Bought at the day-0 close, held 20 trading days, equal-weighted; sum of daily returns minus the "
                  "index", color=MUTED, fontsize=9)
    import matplotlib.dates as mdates

    last = max(frame.index[-1] for frame in series.values())
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_xlim(right=mdates.date2num(last) + 420)
    ax.set_xticks([t for t in ax.get_xticks() if t <= mdates.date2num(last)])
    fig.tight_layout()
    fig.savefig(out)
